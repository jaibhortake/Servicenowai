from __future__ import annotations

import asyncio
import json
from typing import List

from app.config import settings
from app.services.ai_service import get_async_client

from .types import VectorRAGSolutionDocument


class VectorRAGSynthesizer:
    async def answer_from_documents(self, query: str, documents: List[VectorRAGSolutionDocument]) -> str:
        if not documents:
            return "No sufficiently similar solution was found in the solution library for this query."

        snippets = [
            {
                "title": doc.display_name or doc.name or f"Solution {idx + 1}",
                "score": round(float(doc.score), 4),
                "content": doc.content,
            }
            for idx, doc in enumerate(documents[:8])
        ]
        context_json = json.dumps(snippets, ensure_ascii=True, indent=2)

        async_client = get_async_client()
        completion = await asyncio.wait_for(
            async_client.chat.completions.create(
                model=settings.openai_chat_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a retrieval-grounded IT support assistant. "
                            "Use only the provided solution snippets. "
                            "Do not invent remediation steps that are not supported by snippets. "
                            "If snippets are partially related, say what is applicable and what is uncertain."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"User question:\n{query}\n\n"
                            f"Retrieved solutions:\n{context_json}\n\n"
                            "Provide a concise, practical answer and end with a short "
                            "'Sources:' line listing the solution titles you used."
                        ),
                    },
                ],
                temperature=0.1,
                max_tokens=450,
            ),
            timeout=20.0,
        )
        text = str(completion.choices[0].message.content or "").strip()
        if text:
            return text
        return self._fallback_answer(documents)

    def _fallback_answer(self, documents: List[VectorRAGSolutionDocument]) -> str:
        top = documents[:3]
        lines = ["I found related solution guidance in the solution library:"]
        for doc in top:
            title = doc.display_name or doc.name or "Solution"
            lines.append(f"- {title}: {doc.content}")
        sources = ", ".join((doc.display_name or doc.name or "Solution") for doc in top)
        lines.append(f"Sources: {sources}")
        return "\n".join(lines)

