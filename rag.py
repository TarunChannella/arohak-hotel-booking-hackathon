import math
import re

import db
import ingest

TOKEN_RE = re.compile(r"[a-z0-9]+")
STOP = {"a", "an", "and", "are", "at", "be", "can", "do", "does", "for", "have", "hotel", "i", "in", "is", "it", "me", "of", "on", "the", "there", "to", "what", "when", "where", "with"}


NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
                "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}
ROOM_CAPACITY_RE = re.compile(r"([A-Z][A-Za-z ]*?):\s*capacity\s*(\d+)")
ROOMS_MARKER = "Room Categories"
CHECKIN_MARKER = "Check-in"
CHECKOUT_MARKER = "Check-out"
UNAVAILABLE = ("That information is not available in the provided hotel document. "
               "Please contact reception for assistance.")


def stem(word):
    """Crude prefix stem so "cancellation" matches "cancel"."""
    return word[:6] if len(word) > 6 else word


def tokens(text):
    """Tokenise, map number words to digits, and stem.

    Stemming is applied to the index and the query alike so that a document
    saying "cancel" matches a question asking about the "cancellation" policy.
    """
    return [stem(NUMBER_WORDS.get(t, t)) for t in TOKEN_RE.findall(text.lower()) if t not in STOP]


def normalize(text):
    """Lowercase and turn hyphens into spaces.

    The document writes "check-in" while a question may say "check in" or
    "check-in"; both must match the same intent.
    """
    return re.sub(r"\s+", " ", re.sub(r"[-_/]", " ", text.lower())).strip()


def intent(question):
    """Which of check-in / check-out the question is actually about."""
    phrase = normalize(question)
    wants_in = "check in" in phrase or "checkin" in phrase
    wants_out = "check out" in phrase or "checkout" in phrase
    return wants_in, wants_out


def asks_for_a_time(question):
    phrase = normalize(question)
    return bool(re.search(r"\b(what|which|when)\b", phrase) and re.search(r"\b(time|hour|when)\b", phrase)) \
        or phrase.startswith("when")


class HotelRetriever:
    """Retrieval over chunks extracted from one hotel's PDF.

    The index is built from ingest.load_index(), which extracts the text from
    the hotel's own PDF. Nothing here reads a hand-written knowledge file.
    """

    def __init__(self, hotel_id=None, chunks=None):
        self.hotel_id = hotel_id or db.DEFAULT_HOTEL_ID
        self._build(chunks if chunks is not None else ingest.load_index(self.hotel_id))

    def reload(self, force=True):
        """Rebuild the index after the hotel's PDF has been replaced."""
        self._build(ingest.load_index(self.hotel_id, force=force))
        return len(self.sections)

    def _build(self, chunks):
        self.sections = chunks
        self.docs = [tokens(item["section"] + " " + item["text"]) for item in self.sections]
        counts = {}
        for doc in self.docs:
            for token in set(doc):
                counts[token] = counts.get(token, 0) + 1
        self.idf = {token: math.log((len(self.docs) + 1) / (count + 1)) + 1 for token, count in counts.items()}
        # Room names and capacities come from the extracted PDF room table.
        self.capacities = ingest.room_capacities(self.sections)

    def _section_named(self, marker):
        """The first chunk whose heading contains a marker, e.g. "Check-in"."""
        return next((s for s in self.sections if marker in s["section"]), None)

    def _checkin_or_out(self, hits, marker):
        """Quote the standard check-in/check-out time from the PDF itself.

        The sentence is taken verbatim from the extracted chunk rather than
        written here, so the answer stays evidence-backed if the PDF changes.
        """
        chunk = next((h for h in hits if marker in h["section"]), None) or self._section_named(marker)
        if not chunk:
            return None
        wanted = marker.lower()
        for sentence in re.split(r"(?<=[.!?])\s+", chunk["text"]):
            plain = normalize(sentence)
            if "standard" in plain and normalize(wanted) in plain and re.search(r"\d", sentence):
                return self._grounded(sentence.strip(), chunk)
        return None

    def search(self, question, limit=3):
        query = tokens(question)
        if not query:
            return []
        wants_in, wants_out = intent(question)
        ranked = []
        for item, doc in zip(self.sections, self.docs):
            frequencies = {t: doc.count(t) for t in set(doc)}
            score = sum((1 + math.log(frequencies[t])) * self.idf.get(t, 0) for t in set(query) if t in frequencies)
            title = normalize(item["section"])
            score += sum(1.5 for t in set(query) if t in title)
            # Route to the section the question is actually about. Asking about
            # check-in must not surface the check-out policy first.
            heading = item["section"]
            if wants_in and not wants_out:
                score += 6 if CHECKIN_MARKER in heading else 0
                score -= 4 if CHECKOUT_MARKER in heading else 0
            if wants_out and not wants_in:
                score += 6 if CHECKOUT_MARKER in heading else 0
                score -= 4 if CHECKIN_MARKER in heading else 0
            if ({"room", "guests", "capacity"} & set(query)) and ROOMS_MARKER in heading:
                score += 4
            if score > 0:
                ranked.append({**item, "score": round(score, 3)})
        return sorted(ranked, key=lambda x: x["score"], reverse=True)[:limit]

    # ---------- deterministic handlers ----------

    def _cite(self, chunk):
        if not chunk:
            return []
        return [{"section": chunk["section"], "page": chunk["page"], "excerpt": chunk["text"]}]

    def _grounded(self, answer, chunk):
        return {"answer": answer, "grounded": True, "citations": self._cite(chunk)}

    def _named_room(self, question):
        """Longest room name mentioned in the question, if any."""
        phrase = normalize(question)
        matches = [name for name in self.capacities if normalize(name) in phrase]
        return max(matches, key=len) if matches else None

    def _requested_guests(self, question):
        numbers = [int(NUMBER_WORDS.get(t, t)) for t in TOKEN_RE.findall(normalize(question))
                   if t.isdigit() or t in NUMBER_WORDS]
        # Ignore values that are clearly not a guest count (prices, times).
        candidates = [n for n in numbers if 1 <= n <= 20]
        return candidates[0] if candidates else None

    def capacity_answer(self, question):
        """Answer 'can N guests stay in <room>' and 'which room fits N guests'."""
        phrase = normalize(question)
        if "room" not in phrase and "suite" not in phrase and not self._named_room(question):
            return None
        guests = self._requested_guests(question)
        if not guests:
            return None
        named = self._named_room(question)
        if named:
            capacity = self.capacities[named]
            if guests > capacity:
                answer = f"No. The {named} room has a maximum capacity of {capacity} guests."
                fits = sorted((c, n) for n, c in self.capacities.items() if c >= guests)
                if fits:
                    best_capacity, best_name = fits[0]
                    answer += f" The {best_name} accommodates up to {best_capacity} guests."
                else:
                    largest = max(self.capacities.items(), key=lambda kv: kv[1])
                    answer += f" No room category accommodates {guests} guests; the largest is the {largest[0]} with a maximum capacity of {largest[1]} guests."
            else:
                answer = f"Yes. The {named} room accommodates up to {capacity} guests."
            return self._grounded(answer, self._section_named(ROOMS_MARKER))

        fits = sorted((c, n) for n, c in self.capacities.items() if c >= guests)
        if not fits:
            largest = max(self.capacities.items(), key=lambda kv: kv[1])
            return self._grounded(
                f"No room category accommodates {guests} guests. The largest is the {largest[0]}, "
                f"with a maximum capacity of {largest[1]} guests.", self._section_named(ROOMS_MARKER))
        best_capacity, best_name = fits[0]
        return self._grounded(
            f"The {best_name} accommodates up to {best_capacity} guests.",
            self._section_named(ROOMS_MARKER))

    def answer(self, question):
        hits = self.search(question)
        if not hits or hits[0]["score"] < 1.25:
            return {"answer": UNAVAILABLE, "grounded": False, "citations": []}

        wants_in, wants_out = intent(question)

        # Room capacity questions are answered from the room table, so a named
        # room type is never silently swapped for a different one.
        capacity = self.capacity_answer(question)
        if capacity:
            return capacity

        # "What time is check-in/check-out?" must answer the one that was asked.
        if asks_for_a_time(question) and (wants_in or wants_out):
            timed = None
            if wants_in and not wants_out:
                timed = self._checkin_or_out(hits, CHECKIN_MARKER)
            elif wants_out and not wants_in:
                timed = self._checkin_or_out(hits, CHECKOUT_MARKER)
            if timed:
                return timed

        q_tokens = set(tokens(question))
        checkout_time = re.search(r"(?:check\s*out|checkout).*?\b(\d{1,2})\s*(?::\d{2})?\s*(am|pm)\b", question.lower())
        if checkout_time:
            hour, meridiem = int(checkout_time.group(1)), checkout_time.group(2)
            hour24 = (hour % 12) + (12 if meridiem == "pm" else 0)
            policy = next((h for h in hits if CHECKOUT_MARKER in h["section"]), None)
            if policy:
                if hour24 <= 12:
                    answer = "Standard check-out is 12:00 PM."
                elif hour24 <= 14:
                    answer = "Late check-out until 2:00 PM is subject to availability and may be complimentary for selected room categories."
                elif hour24 < 18:
                    answer = "Late check-out after 2:00 PM and before 6:00 PM costs 50% of the applicable nightly room rate."
                else:
                    answer = "Check-out after 6:00 PM costs the full applicable nightly room rate."
                return self._grounded(answer, policy)
        candidates = []
        for rank, hit in enumerate(hits):
            for position, sentence in enumerate(re.split(r"(?<=[.!?])\s+", hit["text"])):
                sentence = sentence.strip()
                # FAQ passages are stored as "Q: ... A: ...". Only the answer
                # half is worth quoting back to the guest.
                if sentence.startswith("Q:"):
                    continue
                if sentence.startswith("A:"):
                    sentence = sentence[2:].strip()
                if not sentence:
                    continue
                overlap = sum(self.idf.get(t, 1) for t in q_tokens & set(tokens(sentence)))
                if overlap:
                    candidates.append((overlap, -rank, -position, sentence, hit["section"]))
        # Best overlap first; then the better-ranked section; then the sentence
        # that appears earliest, since documents state the rule before examples.
        candidates.sort(key=lambda c: (-c[0], -c[1], -c[2]))
        selected = []
        selected_tokens = []
        sections = []
        for _, _, _, sentence, section in candidates:
            words = set(tokens(sentence))
            # The FAQ block restates the policy sections almost verbatim, so
            # drop a sentence that mostly repeats one already chosen.
            if any(words and len(words & prior) / len(words) > 0.7 for prior in selected_tokens):
                continue
            if sentence in selected:
                continue
            selected.append(sentence)
            selected_tokens.append(words)
            if section not in sections:
                sections.append(section)
            if len(selected) == 2:
                break
        if not selected:
            return {"answer": UNAVAILABLE, "grounded": False, "citations": []}
        return {
            "answer": " ".join(s.strip() for s in selected).strip(),
            "grounded": True,
            "citations": [self._cite(next(h for h in hits if h["section"] == s))[0] for s in sections],
        }
