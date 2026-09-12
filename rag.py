import json
import math
import re
from pathlib import Path

TOKEN_RE = re.compile(r"[a-z0-9]+")
STOP = {"a", "an", "and", "are", "at", "be", "can", "do", "does", "for", "have", "hotel", "i", "in", "is", "it", "me", "of", "on", "the", "there", "to", "what", "when", "where", "with"}


def tokens(text):
    number_words = {"one": "1", "two": "2", "three": "3", "four": "4"}
    return [number_words.get(t, t) for t in TOKEN_RE.findall(text.lower()) if t not in STOP]


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

    def search(self, question, limit=3):
        query = tokens(question)
        if not query:
            return []
        phrase = question.lower()
        ranked = []
        for item, doc in zip(self.sections, self.docs):
            frequencies = {t: doc.count(t) for t in set(doc)}
            score = sum((1 + math.log(frequencies[t])) * self.idf.get(t, 0) for t in set(query) if t in frequencies)
            title = item["section"].lower()
            score += sum(1.5 for t in set(query) if t in title)
            if "check in" in phrase and "check-in" in item["text"].lower(): score += 4
            if "check out" in phrase and "check-out" in item["text"].lower(): score += 4
            if ({"room", "guests", "capacity"} & set(query)) and "Room Categories" in item["section"]: score += 4
            if score > 0:
                ranked.append({**item, "score": round(score, 3)})
        return sorted(ranked, key=lambda x: x["score"], reverse=True)[:limit]

    def answer(self, question):
        hits = self.search(question)
        if not hits or hits[0]["score"] < 1.25:
            return {
                "answer": "That information is not available in the provided hotel document. Please contact reception for assistance.",
                "grounded": False,
                "citations": [],
            }

        q_tokens = set(tokens(question))
        checkout_time = re.search(r"(?:check\s*out|checkout).*?\b(\d{1,2})\s*(?::\d{2})?\s*(am|pm)\b", question.lower())
        if checkout_time:
            hour, meridiem = int(checkout_time.group(1)), checkout_time.group(2)
            hour24 = (hour % 12) + (12 if meridiem == "pm" else 0)
            policy = next((h for h in hits if h["section"] == "2. Hotel Policies - Check-out"), None)
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
        if "room" in q_tokens and any(n in q_tokens for n in {"1", "2", "3", "4"}):
            room_hit = next((h for h in hits if h["section"] == "4. Room Categories"), None)
            if room_hit:
                requested = next(n for n in {"1", "2", "3", "4"} if n in q_tokens)
                matching = [s for s in room_hit["text"].split(". ") if f"capacity {requested}" in s.lower()]
                if matching:
                    return {"answer": ". ".join(matching).rstrip(".") + ".", "grounded": True,
                            "citations": [{"section": room_hit["section"], "excerpt": room_hit["text"]}]}
        candidates = []
        for hit in hits:
            for sentence in re.split(r"(?<=[.!?])\s+", hit["text"]):
                overlap = sum(self.idf.get(t, 1) for t in q_tokens & set(tokens(sentence)))
                if overlap:
                    candidates.append((overlap, sentence, hit["section"]))
        candidates.sort(reverse=True)
        selected = []
        sections = []
        for _, sentence, section in candidates:
            if sentence not in selected:
                selected.append(sentence)
                if section not in sections:
                    sections.append(section)
            if len(selected) == 3:
                break
        if not selected:
            return {"answer": "That information is not available in the provided hotel document. Please contact reception for assistance.", "grounded": False, "citations": []}
        return {
            "answer": " ".join(selected),
            "grounded": True,
            "citations": [{"section": s, "excerpt": next(h["text"] for h in hits if h["section"] == s)} for s in sections],
        }
