"""Structured outputs. Every model reply is JSON that must validate against one of these models.

The same Pydantic models generate the JSON schema sent to the provider (first line of defence) and
validate the reply (second line of defence), so the two cannot drift apart.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SubQuestion(BaseModel):
    id: str = Field(pattern=r"^q[0-9]{1,2}$", description="Short id such as q1, q2.")
    ask: str = Field(min_length=3, max_length=400, description="One focused, searchable sub-question.")
    why: str = Field(default="", max_length=400, description="Why the brief needs this answered.")
    depends_on: list[str] = Field(default_factory=list,
                                  description="Ids of sub-questions whose findings this one needs first.")


class ReportSpec(BaseModel):
    max_words: int = Field(default=900, ge=150, le=3000)
    must_cite: bool = True


class Plan(BaseModel):
    brief: str = Field(min_length=3, max_length=600, description="One-paragraph research brief.")
    sub_questions: list[SubQuestion] = Field(min_length=1, max_length=8)
    report: ReportSpec = Field(default_factory=ReportSpec)


class ExtractedQuote(BaseModel):
    source: str = Field(pattern=r"^S[0-9]{1,2}$", description="The source label, e.g. S1.")
    quote: str = Field(min_length=8, max_length=600, description="An exact, verbatim span copied from that source.")


class ExtractedClaim(BaseModel):
    text: str = Field(min_length=5, max_length=500, description="One atomic, checkable factual claim.")
    quotes: list[ExtractedQuote] = Field(min_length=1, max_length=4)


class ResearchStep(BaseModel):
    claims: list[ExtractedClaim] = Field(default_factory=list, max_length=12)
    summary: str = Field(default="", max_length=1500, description="Compressed findings for this sub-question.")
    next_query: str = Field(default="", max_length=200,
                            description="A follow-up web search if the sources are not enough; empty when done.")


class Judgement(BaseModel):
    pair: int = Field(ge=0, description="The pair number being judged.")
    stance: Literal["supports", "contradicts", "insufficient"]
    score: float = Field(ge=0, le=1, description="Confidence in the stance, 0 to 1.")
    rationale: str = Field(default="", max_length=300)


class VerifierOutput(BaseModel):
    judgements: list[Judgement]


class WrittenSentence(BaseModel):
    text: str = Field(min_length=3, max_length=700)
    claim_ids: list[str] = Field(default_factory=list, description="Claim ids (C1, C2...) this sentence rests on.")


class WrittenSection(BaseModel):
    heading: str = Field(min_length=1, max_length=120)
    sentences: list[WrittenSentence] = Field(min_length=1, max_length=40)


class WriterOutput(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    sections: list[WrittenSection] = Field(min_length=1, max_length=10)
