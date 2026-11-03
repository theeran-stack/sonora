"""
MongoDB Integration Preparation Service for SONORA.

Provides connection handling, environment configuration, and collection access
for future Chat Assistant conversation logs and user feedback.

Design Principles:
1. Purely decoupled: Will never crash, block, or degrade existing Django SQLite audio functionality.
2. Zero hardcoded credentials: Read exclusively from environment variables (MONGODB_URI, MONGODB_DB_NAME).
3. Defensive error handling: Gracefully handles missing pymongo package, empty URI, or unreachable cluster.
4. Lazy client singleton: Initializes connection pool on demand.
"""

import os
import logging
from typing import Optional, Tuple, Any

logger = logging.getLogger(__name__)

# Environment variable configuration
MONGODB_URI = os.getenv("MONGODB_URI", "")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "sonora")

# Connection client cache
_mongo_client: Optional[Any] = None


def is_mongo_configured() -> bool:
    """Returns True if MONGODB_URI environment variable is provided and non-empty."""
    uri = os.getenv("MONGODB_URI", MONGODB_URI)
    return bool(uri and uri.strip())


def get_mongo_client():
    """
    Lazily initializes and returns a PyMongo MongoClient instance.
    Returns None if MONGODB_URI is unconfigured, pymongo is not installed, or connection fails.
    """
    global _mongo_client
    if _mongo_client is not None:
        return _mongo_client

    if not is_mongo_configured():
        return None

    try:
        import pymongo
    except ImportError:
        logger.warning("MongoDB is configured via environment, but 'pymongo' library is not installed.")
        return None

    uri = os.getenv("MONGODB_URI", MONGODB_URI)
    try:
        # 3.0s connect timeout prevents blocking web workers
        _mongo_client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=3000, connectTimeoutMS=3000)
        return _mongo_client
    except Exception as exc:
        logger.error(f"Failed to initialize MongoDB client: {exc}")
        return None


def get_mongo_db():
    """
    Returns the configured MongoDB database handle, or None if unavailable.
    """
    client = get_mongo_client()
    if client is None:
        return None
    db_name = os.getenv("MONGODB_DB_NAME", MONGODB_DB_NAME)
    return client[db_name]


def get_chats_collection():
    """
    Returns the 'chats' collection for conversation transcripts, or None if unavailable.
    """
    db = get_mongo_db()
    return db["chats"] if db is not None else None


def get_feedback_collection():
    """
    Returns the 'feedback' collection for user feedback and ratings, or None if unavailable.
    """
    db = get_mongo_db()
    return db["feedback"] if db is not None else None


def save_chat_message(
    session_id: str,
    user_message: str,
    assistant_response: str,
    metadata: Optional[dict] = None
) -> Optional[str]:
    """
    Persists a chat interaction turn into the MongoDB 'chats' collection.
    Returns the inserted document ID as a string, or None if MongoDB is unavailable.
    """
    coll = get_chats_collection()
    if coll is None:
        logger.debug("MongoDB chats collection unavailable; skipping chat persistence.")
        return None

    import datetime
    doc = {
        "session_id": str(session_id or "anonymous_session"),
        "user_message": str(user_message or ""),
        "assistant_response": str(assistant_response or ""),
        "metadata": metadata or {},
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    try:
        result = coll.insert_one(doc)
        return str(result.inserted_id)
    except Exception as exc:
        logger.error(f"Failed to persist chat message to MongoDB: {exc}")
        return None


def save_feedback(
    session_id: str,
    rating: int,
    comments: str = "",
    message_id: str = ""
) -> Optional[str]:
    """
    Persists user feedback into the MongoDB 'feedback' collection.
    Returns the inserted document ID as a string, or None if MongoDB is unavailable.
    """
    coll = get_feedback_collection()
    if coll is None:
        logger.debug("MongoDB feedback collection unavailable; skipping feedback persistence.")
        return None

    import datetime
    doc = {
        "session_id": str(session_id or "anonymous_session"),
        "rating": int(rating),
        "comments": str(comments or "").strip(),
        "message_id": str(message_id or ""),
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    try:
        result = coll.insert_one(doc)
        return str(result.inserted_id)
    except Exception as exc:
        logger.error(f"Failed to persist feedback to MongoDB: {exc}")
        return None


def get_chat_history(session_id: str, limit: int = 50) -> list:
    """
    Retrieves previous chat conversation turns for a session from MongoDB.
    Returns an empty list if MongoDB is unavailable.
    """
    coll = get_chats_collection()
    if coll is None:
        return []

    try:
        cursor = coll.find(
            {"session_id": str(session_id)}
        ).sort("created_at", 1).limit(limit)

        history = []
        for doc in cursor:
            history.append({
                "id": str(doc.get("_id", "")),
                "user_message": doc.get("user_message", ""),
                "assistant_response": doc.get("assistant_response", ""),
                "metadata": doc.get("metadata", {}),
                "created_at": doc.get("created_at", ""),
            })
        return history
    except Exception as exc:
        logger.error(f"Failed to retrieve chat history from MongoDB: {exc}")
        return []


def check_mongo_connection() -> Tuple[bool, str]:
    """
    Tests MongoDB connectivity and returns (is_healthy, status_message).
    Safe to invoke at any time for health checks.
    """
    if not is_mongo_configured():
        return False, "MONGODB_URI is not configured in environment variables."

    try:
        import pymongo
    except ImportError:
        return False, "pymongo package is not installed in the Python environment."

    client = get_mongo_client()
    if client is None:
        return False, "Could not initialize MongoDB client."

    try:
        # Issue quick ping command to verify cluster reachability
        client.admin.command("ping")
        return True, "MongoDB connection established and verified."
    except Exception as exc:
        return False, f"MongoDB connection check failed: {exc}"


def reset_client_cache_for_testing():
    """Utility function to clear cached client connection (primarily for testing)."""
    global _mongo_client
    if _mongo_client is not None:
        try:
            _mongo_client.close()
        except Exception:
            pass
        _mongo_client = None


def verify_mongodb_production_status() -> dict:
    """
    Verifies actual live MongoDB Atlas connectivity and collection operations.
    Returns clear status distinguishing live verified persistence from unconfigured environments.
    """
    if not is_mongo_configured():
        return {
            "configured": False,
            "live_tested": False,
            "status": "unconfigured",
            "message": "MONGODB_URI is not set in environment variables. Running in standalone SQLite mode.",
        }

    is_connected, msg = check_mongo_connection()
    if not is_connected:
        return {
            "configured": True,
            "live_tested": False,
            "status": "connection_failed",
            "message": msg,
        }

    try:
        # Test real read/write on a diagnostic ping document
        db = get_mongo_db()
        test_coll = db["_diagnostics"]
        res = test_coll.insert_one({"test": True, "service": "sonora_verify"})
        test_coll.delete_one({"_id": res.inserted_id})
        return {
            "configured": True,
            "live_tested": True,
            "status": "connected_and_verified",
            "message": "Live MongoDB Atlas cluster connected and verified for read/write persistence.",
        }
    except Exception as exc:
        return {
            "configured": True,
            "live_tested": False,
            "status": "write_test_failed",
            "message": f"Cluster ping succeeded but write test failed: {exc}",
        }


