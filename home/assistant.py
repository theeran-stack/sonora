"""
AI Assistant Knowledge Engine for SONORA.

Grounds all answers strictly in actual SQLite FAQ and ErrorCode records.
Ensures zero technical hallucination by adhering to verified project capabilities.
"""

import re
import logging
from typing import Dict, Any, List, Optional
from .models import FAQ, ErrorCode
from .mongodb import save_chat_message

logger = logging.getLogger(__name__)

STOP_WORDS = {
    "a", "an", "the", "in", "on", "at", "to", "for", "of", "and", "or", "is",
    "are", "was", "were", "be", "been", "it", "this", "that", "how", "what",
    "why", "when", "where", "can", "do", "does", "did", "i", "my", "me", "you"
}


def tokenize(text: str) -> set:
    """Extracts alphanumeric words in lowercase, excluding common stop words."""
    words = re.findall(r"[a-z0-9_]+", text.lower())
    return {w for w in words if w not in STOP_WORDS and len(w) > 1}


def find_matching_error_code(query: str) -> Optional[ErrorCode]:
    """Matches query against ErrorCode database records."""
    q_lower = query.lower().strip()

    # 1. Exact regex match for ERR_... tokens
    code_match = re.search(r"err_[a-z0-9_]+", q_lower)
    if code_match:
        found = ErrorCode.objects.filter(code__iexact=code_match.group(0), is_active=True).first()
        if found:
            return found

    # 2. Check each active error code in database
    for err in ErrorCode.objects.filter(is_active=True):
        if err.code.lower() in q_lower:
            return err
        if str(err.http_status) in q_lower and ("status" in q_lower or "code" in q_lower or "http" in q_lower):
            return err
        if err.title.lower() in q_lower:
            return err

    # 3. Token overlap with title, cause, and code
    q_tokens = tokenize(q_lower)
    if not q_tokens:
        return None

    best_match = None
    best_score = 0

    for err in ErrorCode.objects.filter(is_active=True):
        err_tokens = tokenize(f"{err.code} {err.title} {err.cause}")
        overlap = len(q_tokens & err_tokens)
        if overlap > best_score and overlap >= 2:
            best_score = overlap
            best_match = err

    return best_match


def find_matching_faq(query: str) -> Optional[FAQ]:
    """Matches query against FAQ database records using token similarity."""
    q_lower = query.lower().strip()
    q_tokens = tokenize(q_lower)
    if not q_tokens:
        return None

    # First check substring in question
    for faq in FAQ.objects.filter(is_active=True):
        if faq.question.lower() in q_lower or q_lower in faq.question.lower():
            return faq

    best_match = None
    best_score = 0.0

    for faq in FAQ.objects.filter(is_active=True):
        q_tokens_faq = tokenize(faq.question)
        a_tokens_faq = tokenize(faq.answer)

        # Question matches weighted higher than answer matches
        q_overlap = len(q_tokens & q_tokens_faq)
        a_overlap = len(q_tokens & a_tokens_faq)
        total_score = (q_overlap * 2.5) + (a_overlap * 1.0)

        if total_score > best_score and total_score >= 2.0:
            best_score = total_score
            best_match = faq

    return best_match


def process_assistant_query(query: str, session_id: str = "default") -> Dict[str, Any]:
    """
    Processes user query and returns grounded response with sources.
    Strictly avoids hallucination by citing verified SQL models.
    """
    clean_query = query.strip()
    if not clean_query:
        return {
            "status": "error",
            "reply": "Please enter a question or error code to ask the assistant.",
            "sources": [],
            "session_id": session_id,
        }

    q_lower = clean_query.lower()

    # 1. Greetings & Introductory Prompts
    if q_lower in {"hi", "hello", "hey", "help", "who are you", "start"}:
        reply = (
            "Hello! I am the **SONORA Studio Assistant**.\n\n"
            "I provide verified technical guidance grounded directly in SONORA's architecture, "
            "database FAQs, and error-handling reference.\n\n"
            "**You can ask me questions like:**\n"
            "- *What audio formats does SONORA support?*\n"
            "- *How does HTTP Range streaming and seek scrubbing work?*\n"
            "- *Explain error ERR_PATH_TRAVERSAL_DETECTED*\n"
            "- *What is ERR_STREAM_NOT_FOUND and how do I fix it?*\n"
            "- *How does local-to-cloud synchronization work?*"
        )
        sources = [{"type": "system", "title": "SONORA System Capabilities"}]
        mongo_id = save_chat_message(session_id, clean_query, reply, {"sources": sources})
        return {
            "status": "success",
            "reply": reply,
            "sources": sources,
            "session_id": session_id,
            "mongo_saved": bool(mongo_id),
        }

    # 2. Check for Error Code match first
    err = find_matching_error_code(clean_query)
    if err:
        reply = (
            f"### Error Code: `{err.code}` (HTTP {err.http_status})\n"
            f"**{err.title}**\n\n"
            f"**Description:**\n{err.description}\n\n"
            f"**Root Cause:**\n{err.cause}\n\n"
            f"**Deterministic Recovery Procedure:**\n{err.recovery_step}\n\n"
            f"> *Reference: [View full Error Codes catalogue](/errors/?code={err.code})*"
        )
        sources = [{
            "type": "error_code",
            "id": err.id,
            "code": err.code,
            "http_status": err.http_status,
            "title": err.title,
        }]
        mongo_id = save_chat_message(session_id, clean_query, reply, {"sources": sources})
        return {
            "status": "success",
            "reply": reply,
            "sources": sources,
            "session_id": session_id,
            "mongo_saved": bool(mongo_id),
        }

    # 3. Check for FAQ match
    faq = find_matching_faq(clean_query)
    if faq:
        reply = (
            f"### {faq.question}\n\n"
            f"{faq.answer}\n\n"
            f"*(Category: **{faq.category.capitalize()}** · [View in FAQ catalogue](/faq/?category={faq.category}))*"
        )
        sources = [{
            "type": "faq",
            "id": faq.id,
            "question": faq.question,
            "category": faq.category,
        }]
        mongo_id = save_chat_message(session_id, clean_query, reply, {"sources": sources})
        return {
            "status": "success",
            "reply": reply,
            "sources": sources,
            "session_id": session_id,
            "mongo_saved": bool(mongo_id),
        }

    # 4. Fallback without hallucination
    reply = (
        "I could not locate a matching verified record in SONORA's knowledge database for that question.\n\n"
        "To ensure accuracy, I do not make up technical details. Here is what you can do:\n"
        "1. Browse all documented questions on the **[FAQ Page](/faq/)**.\n"
        "2. Look up application errors in the **[Error Codes Reference](/errors/)**.\n"
        "3. Or submit a question via **[Support Ticket](/api/tickets/)**."
    )
    sources = []
    mongo_id = save_chat_message(session_id, clean_query, reply, {"sources": sources})
    return {
        "status": "success",
        "reply": reply,
        "sources": sources,
        "session_id": session_id,
        "mongo_saved": bool(mongo_id),
    }

