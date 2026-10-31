"""A deterministic stand-in for the web and the model, for tests, offline demos and the simulated
parallelism benchmark. Nothing here is a measurement of model quality.

The scripted model behaves like a careful (but simple) agent:
- plan: one sub-question per entry in ``sub_questions`` (the last one depends on the others);
- research: one claim per source, quoting that source's first full sentence verbatim;
- verify: a pair "supports" when every number in the claim appears in the quote, "contradicts" when the
  quote gives other numbers instead, otherwise "insufficient";
- write: one sentence per claim, citing it, plus one sentence with a made-up id (the citation check must
  drop it).
"""

from __future__ import annotations

import json
import re

from app.llm.providers import ProviderRequest, ProviderResponse, ScriptedProvider
from app.research.web import SearchHit, StaticWeb

_FILLER = ("Visitors queue at the entrance every morning and guided tours run in several languages. "
           "The surrounding park hosts seasonal events and the area is served by public transport. ")

PAGES = {
    "https://alpha-news.com/eiffel": ("Eiffel Tower facts",
        "The Eiffel Tower was completed in 1889 for the World's Fair in Paris. The tower is 330 metres tall "
        "including its antennas. " + _FILLER * 3, "2024-03-01"),
    "https://beta-journal.org/tower-history": ("History of the tower",
        "Completed in 1889, the Eiffel Tower was built for the World's Fair held in Paris that year. "
        "Gustave Eiffel's company designed and built the wrought-iron lattice structure. " + _FILLER[::-1] * 2,
        "2023-06-15"),
    "https://gamma.net/landmarks": ("Landmarks of Paris",
        "The Eiffel Tower is 300 metres tall including its antennas. "
        "It remains one of the most visited paid monuments in the world. " + "Tickets can be booked online. " * 6,
        None),
    "https://alpha-news-mirror.com/eiffel-copy": ("Eiffel Tower facts (syndicated)",
        "The Eiffel Tower was completed in 1889 for the World's Fair in Paris. The tower is 330 metres tall "
        "including its antennas. " + _FILLER * 3, "2024-03-02"),
}


def static_web(latency: float = 0.0, include_mirror: bool = False) -> StaticWeb:
    urls = [u for u in PAGES if include_mirror or "mirror" not in u]
    pages = {}
    for url in urls:
        title, body, published = PAGES[url]
        meta = f'<meta property="article:published_time" content="{published}">' if published else ""
        paragraphs = "".join(f"<p>{s.strip()}.</p>" for s in body.split(". ") if s.strip())
        pages[url] = f"<html><head><title>{title}</title>{meta}</head><body><article>{paragraphs}</article></body></html>"
    hits = [SearchHit(url=u, title=PAGES[u][0]) for u in urls]
    return StaticWeb(pages=pages, default_hits=hits, latency=latency)


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", text))


def _first_sentence(text: str) -> str:
    for sentence in re.split(r"(?<=\.)\s+", text):
        if len(sentence) >= 40:
            return sentence.strip()
    return text.strip()[:200]


def scripted_responder(sub_questions: list[str] | None = None, planted_claim: str | None = None):
    sub_questions = sub_questions or ["When was the Eiffel Tower completed?", "How tall is the Eiffel Tower?"]

    def respond(request: ProviderRequest) -> ProviderResponse:
        if request.feature == "plan":
            subs = [{"id": f"q{i}", "ask": q, "why": "needed for the brief", "depends_on": []}
                    for i, q in enumerate(sub_questions, 1)]
            if len(subs) > 2:
                subs[-1]["depends_on"] = [s["id"] for s in subs[:-1]]
            body = {"brief": "Answer the question from cross-checked sources.", "sub_questions": subs,
                    "report": {"max_words": 400, "must_cite": True}}
        elif request.feature == "research":
            claims = []
            for label, text in re.findall(r'<source id="(S\d+)"[^>]*>\nTitle:[^\n]*\n(.*?)\n</source>',
                                          request.user_text, re.S):
                quote = _first_sentence(text)
                claims.append({"text": quote.rstrip("."), "quotes": [{"source": label, "quote": quote}]})
            if planted_claim and claims:
                claims.append({"text": planted_claim,
                               "quotes": [{"source": "S1", "quote": "This sentence does not appear on any page."}]})
            body = {"claims": claims, "summary": f"{len(claims)} claims found.", "next_query": ""}
        elif request.feature == "verify":
            judgements = []
            for n, claim, quote in re.findall(r"Pair (\d+):\nCLAIM: (.*?)\n<source[^>]*>\nQUOTE: (.*?)\n</source>",
                                              request.user_text, re.S):
                need, have = _numbers(claim), _numbers(quote)
                if need and need <= have:
                    stance, score = "supports", 0.9
                elif need and have and not (need & have):
                    stance, score = "contradicts", 0.85
                elif need and have:
                    stance, score = "contradicts", 0.8
                else:
                    stance, score = "insufficient", 0.5
                judgements.append({"pair": int(n), "stance": stance, "score": score, "rationale": "scripted"})
            body = {"judgements": judgements}
        elif request.feature == "write":
            ids = re.findall(r"^(C\d+) \[", request.user_text, re.M)
            sentences = [{"text": f"Finding {cid}.", "claim_ids": [cid]} for cid in ids]
            sentences.append({"text": "An uncited flourish.", "claim_ids": ["C999"]})
            body = {"title": "Scripted report", "sections": [{"heading": "Answer", "sentences": sentences}]}
        else:
            raise AssertionError(f"unexpected feature {request.feature}")
        text = json.dumps(body)
        return ProviderResponse(text=text, finish_reason="STOP", input_tokens=len(request.user_text) // 4,
                                output_tokens=len(text) // 4)

    return respond


def scripted_provider(latency: float = 0.0, **kwargs) -> ScriptedProvider:
    return ScriptedProvider(scripted_responder(**kwargs), latency=latency)
