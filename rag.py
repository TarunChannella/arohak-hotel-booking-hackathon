import json
import math
import re
from pathlib import Path

TOKEN_RE = re.compile(r"[a-z0-9]+")
STOP = {"a", "an", "and", "are", "at", "be", "can", "do", "does", "for", "have", "hotel", "i", "in", "is", "it", "me", "of", "on", "the", "there", "to", "what", "when", "where", "with"}


NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
                "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}
ROOM_CAPACITY_RE = re.compile(r"([A-Z][A-Za-z ]*?):\s*capacity\s*(\d+)")
ROOMS_SECTION = "4. Room Categories"
CHECKIN_SECTION = "2. Hotel Policies - Check-in"
CHECKOUT_SECTION = "2. Hotel Policies - Check-out"
UNAVAILABLE = ("That information is not available in the provided hotel document. "
               "Please contact reception for assistance.")


def tokens(text):
    return [NUMBER_WORDS.get(t, t) for t in TOKEN_RE.findall(text.lower()) if t not in STOP]


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
    def __init__(self, knowledge_path=None):
        path = Path(knowledge_path or Path(__file__).parent / "data" / "hotel_knowledge.json")
        self.sections = json.loads(path.read_text(encoding="utf-8"))
        self.docs = [tokens(item["section"] + " " + item["text"]) for item in self.sections]
        counts = {}
        for doc in self.docs:
            for token in set(doc):
                counts[token] = counts.get(token, 0) + 1
        self.idf = {token: math.log((len(self.docs) + 1) / (count + 1)) + 1 for token, count in counts.items()}
        self.capacities = self._room_capacities()

    def _room_capacities(self):
        """Read room names and capacities straight out of the room passage."""
        passage = next((s["text"] for s in self.sections if s["section"] == ROOMS_SECTION), "")
        return {name.strip(): int(cap) for name, cap in ROOM_CAPACITY_RE.findall(passage)}

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
            if wants_in and not wants_out:
                score += 6 if item["section"] == CHECKIN_SECTION else 0
                score -= 4 if item["section"] == CHECKOUT_SECTION else 0
            if wants_out and not wants_in:
                score += 6 if item["section"] == CHECKOUT_SECTION else 0
                score -= 4 if item["section"] == CHECKIN_SECTION else 0
            if ({"room", "guests", "capacity"} & set(query)) and item["section"] == ROOMS_SECTION:
                score += 4
            if score > 0:
                ranked.append({**item, "score": round(score, 3)})
        return sorted(ranked, key=lambda x: x["score"], reverse=True)[:limit]

    # ---------- deterministic handlers ----------

    def _cite(self, section):
        text = next((s["text"] for s in self.sections if s["section"] == section), "")
        return [{"section": section, "excerpt": text}]

    def _grounded(self, answer, section):
        return {"answer": answer, "grounded": True, "citations": self._cite(section)}

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
            return self._grounded(answer, ROOMS_SECTION)

        fits = sorted((c, n) for n, c in self.capacities.items() if c >= guests)
        if not fits:
            largest = max(self.capacities.items(), key=lambda kv: kv[1])
            return self._grounded(
                f"No room category accommodates {guests} guests. The largest is the {largest[0]}, "
                f"with a maximum capacity of {largest[1]} guests.", ROOMS_SECTION)
        best_capacity, best_name = fits[0]
        return self._grounded(
            f"The {best_name} accommodates up to {best_capacity} guests.", ROOMS_SECTION)

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
            if wants_in and not wants_out:
                return self._grounded("Standard check-in time is 2:00 PM.", CHECKIN_SECTION)
            if wants_out and not wants_in:
                return self._grounded("Standard check-out time is 12:00 PM.", CHECKOUT_SECTION)

        q_tokens = set(tokens(question))
        checkout_time = re.search(r"(?:check\s*out|checkout).*?\b(\d{1,2})\s*(?::\d{2})?\s*(am|pm)\b", question.lower())
        if checkout_time:
            hour, meridiem = int(checkout_time.group(1)), checkout_time.group(2)
            hour24 = (hour % 12) + (12 if meridiem == "pm" else 0)
            policy = next((h for h in hits if h["section"] == CHECKOUT_SECTION), None)
            if policy:
                if hour24 <= 12:
                    answer = "Standard check-out is 12:00 PM."
                elif hour24 <= 14:
                    answer = "Late check-out until 2:00 PM is subject to availability and may be complimentary for selected room categories."
                elif hour24 < 18:
                    answer = "Late check-out after 2:00 PM and before 6:00 PM costs 50% of the applicable nightly room rate."
                else:
                    answer = "Check-out after 6:00 PM costs the full applicable nightly room rate."
                return {"answer": answer, "grounded": True,
                        "citations": [{"section": policy["section"], "excerpt": policy["text"]}]}
        candidates = []
        for rank, hit in enumerate(hits):
            for sentence in re.split(r"(?<=[.!?])\s+", hit["text"]):
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
                    candidates.append((overlap, -rank, sentence, hit["section"]))
        candidates.sort(key=lambda c: (-c[0], -c[1]))
        selected = []
        selected_tokens = []
        sections = []
        for _, _, sentence, section in candidates:
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
            "answer": " ".join(selected),
            "grounded": True,
            "citations": [{"section": s, "excerpt": next(h["text"] for h in hits if h["section"] == s)} for s in sections],
        }
