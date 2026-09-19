import os
import json
import ipaddress
import platform
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from openai import OpenAI, OpenAIError
from error_logger import log_static_methods
from runtime_paths import BUNDLE_DIR, writable_path


DEFAULT_API_URL = "https://api.swarif.com"
LOGIN_PATH = "/api/auth/login"
GET_USER_PATH = "/api/user/get-user"
GET_FRONTEND_CONFIG_PATH = "/api/swarif/get-config/fe"
GET_LLM_API_KEY_PATH = "/api/swarif/get-llm-api-key"
GET_CHAT_PATH = "/api/client-app/get-chat"
SEND_MESSAGE_PATH = "/api/client-app/send-message"
FETCH_JOB_PATH = "/api/client-app/job/fetch-job"
CREATE_JOB_PATH = "/api/client-app/job/create-job"
LIST_CUSTOM_SKILLS_PATH = "/api/users/get/list-custom-skills"
ALL_CUSTOM_SKILLS_PATH = "/api/users/all-custom-skills"
LEARNING_START_MESSAGE = 'Send "start" to start teaching the AI.'
DEFAULT_SESSION_PATH = writable_path("sessions.json")
FRONTEND_CONFIG_PATH = writable_path("frontend_config.json")
MEMORY_PATH = writable_path("memory.md")
BEHAVIOR_PATH = BUNDLE_DIR / "FRONT_AGENT_BEHAVIOR.md"
CONTEXT_MEMORY_PATH = writable_path("context_memory.json")
CHAT_PATH = writable_path("chats.json")
EXTRA_DATA_PATH = writable_path("extra_data.json")
CHAT_FILE_LOCK = threading.RLock()
SESSION_FILE_LOCK = threading.RLock()
CONFIG_FILE_LOCK = threading.RLock()
LLM_API_KEY_LOCK = threading.RLock()
_LLM_API_KEY_CACHE = {}
AGENT_LOG_PATH = writable_path("agent_log.txt")
AGENT_LOG_LOCK = threading.RLock()
MAX_AGENT_LOG_BYTES = 1024 * 1024

for _path, _initial_content in (
    (MEMORY_PATH, ""),
    (CHAT_PATH, "[]\n"),
    (EXTRA_DATA_PATH, "{}\n"),
    (
        CONTEXT_MEMORY_PATH,
        '{\n  "current_step": "decide_action",\n  "step_history": [],\n  "step_count": 0\n}\n',
    ),
):
    if not _path.exists():
        _path.write_text(_initial_content, encoding="utf-8")


def log_llm_call(provider, model, step, timeout, started_at, response=None, error=None):
    """Append one metadata-only JSON line describing an LLM API call."""
    usage = response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "llm_call",
        "provider": provider,
        "model": model,
        "step": step or "unspecified",
        "timeout_seconds": timeout,
        "duration_ms": round((time.monotonic() - started_at) * 1000),
        "status": "failed" if error is not None else "completed",
    }
    if usage is not None:
        if isinstance(usage, dict):
            entry["input_tokens"] = usage.get("input_tokens")
            entry["output_tokens"] = usage.get("output_tokens")
            entry["total_tokens"] = usage.get("total_tokens")
        else:
            entry["input_tokens"] = getattr(usage, "prompt_tokens", None)
            entry["output_tokens"] = getattr(usage, "completion_tokens", None)
            entry["total_tokens"] = getattr(usage, "total_tokens", None)
    if error is not None:
        entry["error_type"] = type(error).__name__

    with AGENT_LOG_LOCK:
        with AGENT_LOG_PATH.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(entry, ensure_ascii=False) + "\n")
        try:
            if AGENT_LOG_PATH.stat().st_size > MAX_AGENT_LOG_BYTES:
                retained = AGENT_LOG_PATH.read_bytes()[-MAX_AGENT_LOG_BYTES:]
                first_newline = retained.find(b"\n")
                if first_newline >= 0:
                    retained = retained[first_newline + 1:]
                AGENT_LOG_PATH.write_bytes(retained)
        except FileNotFoundError:
            pass


def parse_llm_json_object(content):
    """Extract the first valid JSON object from an LLM response."""
    if isinstance(content, dict):
        return content

    if isinstance(content, list):
        text_parts = []
        for part in content:
            if isinstance(part, str):
                text_parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                text_parts.append(part["text"])
            elif isinstance(getattr(part, "text", None), str):
                text_parts.append(part.text)
        content = "\n".join(text_parts)

    if not isinstance(content, str) or not content.strip():
        raise ValueError("LLM API returned no textual JSON content")

    decoder = json.JSONDecoder()
    for index, character in enumerate(content):
        if character != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(content, index)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            return candidate

    raise ValueError("LLM API response does not contain a valid JSON object")


def compact_user_jobs_for_llm(user_jobs, max_jobs_per_group=20, max_text_chars=2000):
    """Keep only bounded job-status fields needed by the intake agent."""
    if not isinstance(user_jobs, dict):
        return user_jobs

    allowed_fields = (
        "id",
        "title",
        "task_title",
        "type",
        "job_type",
        "status",
        "created_time",
        "updated_time",
        "completed_time",
        "completion_feedback",
        "error",
        "message",
    )

    def compact_job(job):
        if not isinstance(job, dict):
            return {"value": str(job)[:max_text_chars]}
        compacted = {}
        for field in allowed_fields:
            value = job.get(field)
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool)):
                compacted[field] = (
                    value[:max_text_chars] if isinstance(value, str) else value
                )
        return compacted

    return {
        "user_id": user_jobs.get("user_id"),
        "pending_jobs": [
            compact_job(job)
            for job in (user_jobs.get("pending_jobs") or [])[:max_jobs_per_group]
        ],
        "visibility_note": str(user_jobs.get("visibility_note", ""))[:max_text_chars],
    }



@log_static_methods
class Agent:
    @staticmethod
    def _report_step(response, on_step, fallback="Thinking"):
        """Emit a safe one-to-five-word summary for the current agent step."""
        if on_step is None:
            return
        summary = response.get("short_summary") if isinstance(response, dict) else None
        if not isinstance(summary, str) or not summary.strip():
            summary = fallback
        words = summary.strip().split()
        on_step(" ".join(words[:5]) or "Thinking")

    @staticmethod
    def connection_settings():
        """Return persistent machine-level agent connection settings."""
        config = Agent.frontend_config()
        agent_is_local = Agent._config_bool(
            config.get("SWARIF_AGENT_IS_LOCAL", False)
        )
        agent_ip = str(config.get("SWARIF_AGENT_IP") or "").strip()
        if agent_is_local:
            agent_ip = "127.0.0.1"
        try:
            server_port = int(config.get("SWARIF_AGENT_PORT", 8767))
            if not 1 <= server_port <= 65535:
                raise ValueError
        except (TypeError, ValueError):
            server_port = 8767
        return {
            "agent_ip": agent_ip,
            "server_ip": agent_ip,
            "server_port": server_port,
            "agent_is_local": agent_is_local,
        }

    @staticmethod
    def _config_bool(value):
        if isinstance(value, bool):
            return value
        return str(value).strip().casefold() in {"1", "true", "yes", "on"}

    @staticmethod
    def _read_config_cache():
        with CONFIG_FILE_LOCK:
            try:
                cached = json.loads(FRONTEND_CONFIG_PATH.read_text(encoding="utf-8"))
            except (FileNotFoundError, OSError, json.JSONDecodeError):
                return {"remote": {}, "overrides": {}}
        if not isinstance(cached, dict):
            return {"remote": {}, "overrides": {}}
        # Accept the original flat cache format if one was created by a preview build.
        if "remote" not in cached and "overrides" not in cached:
            return {"remote": cached, "overrides": {}}
        return {
            "remote": cached.get("remote") if isinstance(cached.get("remote"), dict) else {},
            "overrides": (
                cached.get("overrides")
                if isinstance(cached.get("overrides"), dict)
                else {}
            ),
        }

    @staticmethod
    def frontend_config(refresh_if_missing=True):
        """Return cached backend configuration plus persistent local overrides."""
        cached = Agent._read_config_cache()
        remote = cached["remote"]
        if refresh_if_missing and not remote:
            try:
                return Agent.refresh_frontend_config()
            except Exception:
                pass
        return {**remote, **cached["overrides"]}

    @staticmethod
    def refresh_frontend_config(timeout=10):
        """Fetch frontend-safe configuration once and persist it locally."""
        endpoint = f"{DEFAULT_API_URL.rstrip('/')}{GET_FRONTEND_CONFIG_PATH}"
        request = Request(
            endpoint,
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                remote = json.load(response)
        except HTTPError as error:
            try:
                body = json.loads(error.read().decode("utf-8"))
                detail = body.get("message") or body.get("error", {}).get("message")
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                detail = None
            raise RuntimeError(
                f"Configuration API returned HTTP {error.code}: {detail or error.reason}"
            ) from error
        except URLError as error:
            raise ConnectionError(
                f"Could not connect to the configuration API at {endpoint}: {error.reason}"
            ) from error
        except (TimeoutError, OSError) as error:
            raise ConnectionError(
                f"Could not connect to the configuration API at {endpoint}: {error}"
            ) from error
        except json.JSONDecodeError as error:
            raise RuntimeError("Configuration API returned invalid JSON") from error
        if not isinstance(remote, dict) or not remote:
            raise RuntimeError("Configuration API returned an empty configuration")

        cached = Agent._read_config_cache()
        payload = {"remote": remote, "overrides": cached["overrides"]}
        with CONFIG_FILE_LOCK:
            FRONTEND_CONFIG_PATH.write_text(
                json.dumps(payload, indent=2) + "\n", encoding="utf-8"
            )
        return {**remote, **cached["overrides"]}

    @staticmethod
    def get_llm_api_key(timeout=10, force_refresh=False):
        """Fetch this account's organization LLM key and retain it in memory only."""
        session = Agent.read_session()
        if session is False:
            raise RuntimeError("Your session has expired. Please sign in again.")
        account_id = session.get("user_id") or session.get("id")
        token = session.get("token")
        account_type = session.get("type") or "user"
        if account_type not in {"user", "org"}:
            account_type = "user"
        if not isinstance(account_id, str) or not account_id.strip():
            raise RuntimeError("The session is missing the account ID")
        if not isinstance(token, str) or not token.strip():
            raise RuntimeError("Your session has expired. Please sign in again.")

        cache_key = (account_type, account_id.strip(), token.strip())
        with LLM_API_KEY_LOCK:
            if not force_refresh and cache_key in _LLM_API_KEY_CACHE:
                return _LLM_API_KEY_CACHE[cache_key]

        endpoint = f"{DEFAULT_API_URL.rstrip('/')}{GET_LLM_API_KEY_PATH}"
        payload = {
            "id": account_id.strip(),
            "token": token.strip(),
            "type": account_type,
        }
        request = Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                result = json.load(response)
        except HTTPError as error:
            try:
                body = json.loads(error.read().decode("utf-8"))
                detail = body.get("message") or body.get("error", {}).get("message")
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                detail = None
            raise RuntimeError(
                f"LLM key API returned HTTP {error.code}: {detail or error.reason}"
            ) from error
        except URLError as error:
            raise ConnectionError(
                f"Could not connect to the LLM key API at {endpoint}: {error.reason}"
            ) from error
        except (TimeoutError, OSError) as error:
            raise ConnectionError(
                f"Could not connect to the LLM key API at {endpoint}: {error}"
            ) from error
        except json.JSONDecodeError as error:
            raise RuntimeError("LLM key API returned invalid JSON") from error

        api_key = result.get("llm_api_key") if isinstance(result, dict) else None
        if not isinstance(api_key, str) or not api_key.strip():
            raise RuntimeError("LLM key API response is missing llm_api_key")
        with LLM_API_KEY_LOCK:
            _LLM_API_KEY_CACHE.clear()
            _LLM_API_KEY_CACHE[cache_key] = api_key.strip()
        return api_key.strip()

    @staticmethod
    def clear_llm_api_key_cache():
        with LLM_API_KEY_LOCK:
            _LLM_API_KEY_CACHE.clear()

    @staticmethod
    def server_connection_config(session_path=None):
        """Combine login identity with machine-level connection settings."""
        session = Agent.read_session(session_path)
        if session is False:
            return {}
        return {**session, **Agent.connection_settings()}

    @staticmethod
    def save_connection_settings(agent_ip, server_port, agent_is_local, session_path=None):
        """Persist agent networking as local frontend-config overrides."""
        value = "127.0.0.1" if agent_is_local else str(agent_ip).strip()
        try:
            ipaddress.IPv4Address(value)
        except ipaddress.AddressValueError as error:
            raise ValueError("agent_ip must be a valid IPv4 address") from error
        if isinstance(server_port, bool):
            raise ValueError("server_port must be between 1 and 65535")
        try:
            port = int(server_port)
        except (TypeError, ValueError) as error:
            raise ValueError("server_port must be between 1 and 65535") from error
        if not 1 <= port <= 65535:
            raise ValueError("server_port must be between 1 and 65535")

        settings = {
            "SWARIF_AGENT_IP": value,
            "SWARIF_AGENT_PORT": port,
            "SWARIF_AGENT_IS_LOCAL": bool(agent_is_local),
        }
        cached = Agent._read_config_cache()
        cached["overrides"].update(settings)
        with CONFIG_FILE_LOCK:
            FRONTEND_CONFIG_PATH.write_text(
                json.dumps(cached, indent=2) + "\n", encoding="utf-8"
            )
        Agent.remove_session_connection_settings(session_path)
        return Agent.connection_settings()

    @staticmethod
    def remove_session_connection_settings(session_path=None):
        """Remove connection fields previously stored in sessions.json."""
        path = Path(session_path) if session_path else DEFAULT_SESSION_PATH
        with SESSION_FILE_LOCK:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, OSError, json.JSONDecodeError):
                return
            target = payload.get("session") if isinstance(payload, dict) else None
            if not isinstance(target, dict):
                target = payload
            if not isinstance(target, dict):
                return
            changed = False
            for key in ("agent_ip", "server_ip", "server_port", "agent_is_local"):
                if key in target:
                    target.pop(key)
                    changed = True
            if changed:
                path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def migrate_session_connection_settings(session_path=None):
        """Move legacy session networking fields to the config cache once."""
        session = Agent.read_session(session_path)
        if session is False:
            return Agent.connection_settings()
        existing_ip = Agent.connection_settings()["agent_ip"]
        legacy_ip = session.get("agent_ip") or session.get("server_ip")
        if not existing_ip and isinstance(legacy_ip, str) and legacy_ip.strip():
            return Agent.save_connection_settings(
                legacy_ip,
                session.get("server_port", 8767),
                bool(session.get("agent_is_local")),
                session_path,
            )
        Agent.remove_session_connection_settings(session_path)
        return Agent.connection_settings()

    @staticmethod
    def user_files_path(session_path=None):
        """Return the current user's platform-specific Swarif Files directory."""
        session = Agent.read_session(session_path)
        if session is False:
            raise RuntimeError("Your session has expired. Please sign in again.")

        user_id = session["user_id"]
        agent_ip = Agent.connection_settings()["agent_ip"]
        if not isinstance(agent_ip, str) or not agent_ip.strip():
            raise RuntimeError("The Agent IP address is missing from Settings.")

        try:
            address = ipaddress.IPv4Address(agent_ip.strip())
        except ipaddress.AddressValueError as error:
            raise RuntimeError("The saved Agent IP address is invalid.") from error

        local_addresses = {"127.0.0.1"}
        try:
            local_addresses.update(
                result[4][0]
                for result in socket.getaddrinfo(
                    socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM
                )
            )
        except socket.gaierror:
            pass
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect((str(address), 9))
                local_addresses.add(probe.getsockname()[0])
        except OSError:
            pass

        if address.is_loopback or str(address) in local_addresses:
            system = platform.system()
            if system == "Windows":
                root = Path(r"C:\ProgramData\Swarif\Files")
            elif system == "Darwin":
                root = Path("/Library/Application Support/Swarif/Files")
            else:
                root = Path("/var/lib/swarif/files")
            return root / user_id

        return Path(f"//{address}/swarif/{user_id}")

    @staticmethod
    def list_user_files(session_path=None, max_entries=500):
        """List the signed-in user's files without reading their contents."""
        if isinstance(max_entries, bool) or not isinstance(max_entries, int) or max_entries < 1:
            raise ValueError("max_entries must be a positive integer")

        root = Agent.user_files_path(session_path)
        if not root.is_dir():
            return {
                "status": "unavailable",
                "folder": str(root),
                "entries": [],
                "message": "The user's Swarif Files folder is not available.",
            }

        entries = []
        truncated = False
        for current_root, directory_names, file_names in os.walk(root, followlinks=False):
            directory_names.sort(key=str.casefold)
            file_names.sort(key=str.casefold)
            current_path = Path(current_root)
            names = [(name, True) for name in directory_names]
            names.extend((name, False) for name in file_names)
            for name, is_directory in names:
                path = current_path / name
                try:
                    relative_path = path.relative_to(root).as_posix()
                    stat = path.lstat()
                except OSError:
                    continue
                entry = {
                    "path": relative_path,
                    "type": "symlink" if path.is_symlink() else ("directory" if is_directory else "file"),
                    "modified_time": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat(),
                }
                if not is_directory:
                    entry["size_bytes"] = stat.st_size
                entries.append(entry)
                if len(entries) >= max_entries:
                    truncated = True
                    break
            if truncated:
                break

        return {
            "status": "ok",
            "folder": str(root),
            "entries": entries,
            "truncated": truncated,
        }

    @staticmethod
    def update_session(values, session_path=None):
        """Merge values into the active session and persist them as JSON."""
        if not isinstance(values, dict):
            raise ValueError("values must be a dictionary")

        path = Path(session_path) if session_path else DEFAULT_SESSION_PATH
        with SESSION_FILE_LOCK:
            session = Agent.read_session(path)
            if session is False:
                raise RuntimeError("Your session has expired. Please sign in again.")
            session.update(values)
            path.write_text(
                json.dumps(session, indent=2) + "\n",
                encoding="utf-8",
            )
        return session

    @staticmethod
    def read_local_chat():
        """Return the local chat JSON array."""
        with CHAT_FILE_LOCK:
            try:
                raw_chat = CHAT_PATH.read_text(encoding="utf-8")
                local_chat = json.loads(raw_chat) if raw_chat.strip() else []
            except (OSError, json.JSONDecodeError) as error:
                raise RuntimeError("Local chat file is invalid") from error
            if not isinstance(local_chat, list):
                raise RuntimeError("Local chat file must contain a JSON array")
            return local_chat

    @staticmethod
    def write_local_chat(messages):
        """Replace the local chat JSON array."""
        if not isinstance(messages, list):
            raise ValueError("messages must be a list")
        with CHAT_FILE_LOCK:
            CHAT_PATH.write_text(
                json.dumps(messages, indent=2) + "\n",
                encoding="utf-8",
            )

    @staticmethod
    def append_local_chat(message):
        """Append one message to the local chat JSON array."""
        if not isinstance(message, dict):
            raise ValueError("message must be a dictionary")
        with CHAT_FILE_LOCK:
            messages = Agent.read_local_chat()
            messages.append(message)
            Agent.write_local_chat(messages)
        return message

    @staticmethod
    def replace_local_chat(message_id, replacement):
        """Replace a local message by ID without dropping concurrent messages."""
        if not isinstance(replacement, dict):
            raise ValueError("replacement must be a dictionary")
        with CHAT_FILE_LOCK:
            messages = Agent.read_local_chat()
            for index, message in enumerate(messages):
                if isinstance(message, dict) and message.get("id") == message_id:
                    messages[index] = replacement
                    Agent.write_local_chat(messages)
                    return replacement
        return False

    @staticmethod
    def refresh_session_user():
        """Fetch current user details and merge them into the saved session."""
        session = Agent.read_session()
        if session is False:
            return False

        user_id = session["user_id"]
        org_id = session.get("org_id")
        token = session.get("token")
        if not isinstance(org_id, str) or not org_id.strip():
            raise RuntimeError("The saved session is missing org_id. Please sign in again.")
        if not isinstance(token, str) or not token:
            raise RuntimeError("The saved session is missing its token")

        endpoint = (
            f"{DEFAULT_API_URL.rstrip('/')}"
            f"{GET_USER_PATH}"
        )
        payload = json.dumps(
            {"org_id": org_id.strip(), "user_id": user_id}
        ).encode("utf-8")
        request = Request(
            endpoint,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                user_details = json.load(response)
        except HTTPError as error:
            try:
                error_body = json.loads(error.read().decode("utf-8"))
                message = error_body.get("message", str(error))
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                message = str(error)
            raise RuntimeError(f"Unable to load user details: {message}") from error
        except URLError as error:
            raise ConnectionError(
                f"Could not connect to Swarif at {endpoint}: {error.reason}"
            ) from error
        except json.JSONDecodeError as error:
            raise RuntimeError("Get-user API returned invalid JSON") from error

        if not isinstance(user_details, dict) or user_details.get("id") != user_id:
            raise RuntimeError("Get-user API returned an invalid user")
        if not isinstance(user_details.get("name"), str) or not user_details["name"].strip():
            raise RuntimeError("Get-user API response is missing the user name")

        updated_session = {**session, **user_details, "user_id": user_id}
        DEFAULT_SESSION_PATH.write_text(
            json.dumps(updated_session, indent=2) + "\n",
            encoding="utf-8",
        )
        return updated_session

    @staticmethod
    def login(email, password, remember_me=False):
        """Authenticate a user and persist the returned session data."""
        Agent.clear_llm_api_key_cache()
        if not isinstance(email, str) or not email.strip():
            raise ValueError("email must be a non-empty string")
        if not isinstance(password, str) or not password:
            raise ValueError("password must be a non-empty string")
        if not isinstance(remember_me, bool):
            raise ValueError("remember_me must be a boolean")

        endpoint = (
            f"{DEFAULT_API_URL.rstrip('/')}"
            f"{LOGIN_PATH}"
        )
        payload = json.dumps(
            {
                "email": email.strip(),
                "password": password,
                "type": "user",
            }
        ).encode("utf-8")
        request = Request(
            endpoint,
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=10) as response:
                login_result = json.load(response)
        except HTTPError as error:
            try:
                error_body = json.loads(error.read().decode("utf-8"))
                message = error_body.get("message", str(error))
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                message = str(error)
            raise RuntimeError(message) from error
        except URLError as error:
            raise ConnectionError(
                f"Could not connect to Swarif at {endpoint}: {error.reason}"
            ) from error
        except json.JSONDecodeError as error:
            raise RuntimeError("Login API returned invalid JSON") from error

        if not isinstance(login_result, dict):
            raise RuntimeError("Login API returned an invalid response")
        token = login_result.get("token")
        user_id = login_result.get("user_id")
        if not isinstance(token, str) or not token:
            raise RuntimeError("Login API response is missing the token")
        if not isinstance(user_id, str) or not user_id:
            raise RuntimeError("Login API response is missing user_id")

        org_id = login_result.get("org_id")
        if not isinstance(org_id, str) or not org_id.strip():
            raise RuntimeError("Login API response is missing org_id")

        user_endpoint = (
            f"{DEFAULT_API_URL.rstrip('/')}"
            f"{GET_USER_PATH}"
        )
        user_payload = json.dumps(
            {"org_id": org_id.strip(), "user_id": user_id}
        ).encode("utf-8")
        user_request = Request(
            user_endpoint,
            data=user_payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        try:
            with urlopen(user_request, timeout=10) as response:
                user_details = json.load(response)
        except HTTPError as error:
            try:
                error_body = json.loads(error.read().decode("utf-8"))
                message = error_body.get("message", str(error))
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                message = str(error)
            raise RuntimeError(f"Unable to load user details: {message}") from error
        except URLError as error:
            raise ConnectionError(
                f"Could not connect to Swarif at {user_endpoint}: {error.reason}"
            ) from error
        except json.JSONDecodeError as error:
            raise RuntimeError("Get-user API returned invalid JSON") from error

        if not isinstance(user_details, dict):
            raise RuntimeError("Get-user API returned an invalid response")
        if user_details.get("id") != user_id:
            raise RuntimeError("Get-user API returned the wrong user")
        if not isinstance(user_details.get("name"), str) or not user_details["name"].strip():
            raise RuntimeError("Get-user API response is missing the user name")

        session = {
            **user_details,
            "token": token,
            "user_id": user_id,
            "id": user_id,
            "type": "user",
            "remember_me": remember_me,
            "expires_at": (
                (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
                if remember_me
                else None
            ),
        }
        DEFAULT_SESSION_PATH.write_text(
            json.dumps(session, indent=2) + "\n",
            encoding="utf-8",
        )
        return session

    @staticmethod
    def generate_ai_content(
        user_prompt,
        system_prompt,
        timeout=60,
        step=None,
    ):
        """Generate structured content locally with backend-provided configuration."""
        def prompt_text(prompt, name):
            if isinstance(prompt, str):
                if not prompt.strip():
                    raise ValueError(f"{name} must not be empty")
                return prompt.strip()
            if not isinstance(prompt, (dict, list)):
                raise ValueError(f"{name} must be a string, object, or array")
            return json.dumps(prompt, ensure_ascii=False)

        user_content = prompt_text(user_prompt, "user_prompt")
        system_content = prompt_text(system_prompt, "system_prompt")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("timeout must be a positive number")

        config = Agent.frontend_config()
        provider_name = str(config.get("LLM_PROVIDER") or "").strip().lower()
        if provider_name not in {"deepseek", "gemini", "openrouter"}:
            # Refresh once in case the persistent cache predates LLM configuration.
            config = Agent.refresh_frontend_config()
            provider_name = str(config.get("LLM_PROVIDER") or "").strip().lower()
        if provider_name not in {"deepseek", "gemini", "openrouter"}:
            raise RuntimeError("Frontend configuration has an unsupported LLM_PROVIDER")

        prefix = provider_name.upper()
        base_url = str(
            config.get(f"{prefix}_BASE_URL") or config.get("LLM_BASE_URL") or ""
        ).strip()
        selected_model = str(
            config.get(f"{prefix}_MODEL") or config.get("LLM_MODEL") or ""
        ).strip()
        if not base_url or not selected_model:
            config = Agent.refresh_frontend_config()
            base_url = str(
                config.get(f"{prefix}_BASE_URL") or config.get("LLM_BASE_URL") or ""
            ).strip()
            selected_model = str(
                config.get(f"{prefix}_MODEL") or config.get("LLM_MODEL") or ""
            ).strip()
        if not base_url:
            raise RuntimeError(f"Frontend configuration is missing {prefix}_BASE_URL")
        if not selected_model:
            raise RuntimeError(f"Frontend configuration is missing {prefix}_MODEL")

        api_key = Agent.get_llm_api_key()
        default_headers = None
        if provider_name == "openrouter":
            default_headers = {
                "HTTP-Referer": str(config.get("OPENROUTER_SITE_URL") or "https://swarif.com"),
                "X-Title": str(config.get("OPENROUTER_APP_NAME") or "Swarif"),
            }
        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            default_headers=default_headers,
        )
        request_options = {
            "model": selected_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"{system_content}\n\n"
                        "Your entire response must be exactly one valid JSON object. "
                        "Do not include Markdown fences, commentary, or any text before "
                        "or after the JSON object."
                    ),
                },
                {"role": "user", "content": user_content},
            ],
            "response_format": {"type": "json_object"},
            "stream": False,
        }
        if provider_name == "openrouter":
            request_options["extra_body"] = {
                "reasoning": {
                    "effort": str(
                        config.get("OPENROUTER_REASONING_EFFORT") or "low"
                    ).strip().lower()
                }
            }
        started_at = time.monotonic()
        try:
            response = client.chat.completions.create(**request_options)
        except OpenAIError as error:
            log_llm_call(provider_name, selected_model, step, timeout, started_at, error=error)
            raise RuntimeError(
                f"{provider_name.title()} API request failed: {error}"
            ) from error
        except Exception as error:
            log_llm_call(provider_name, selected_model, step, timeout, started_at, error=error)
            raise
        if not response.choices:
            error = RuntimeError(f"{provider_name.title()} API returned an empty response")
            log_llm_call(provider_name, selected_model, step, timeout, started_at, response=response, error=error)
            raise error
        try:
            result = parse_llm_json_object(response.choices[0].message.content)
        except ValueError as error:
            log_llm_call(provider_name, selected_model, step, timeout, started_at, response=response, error=error)
            raise RuntimeError(
                f"{provider_name.title()} API returned invalid JSON: {error}"
            ) from error
        log_llm_call(provider_name, selected_model, step, timeout, started_at, response=response)
        return result

    @staticmethod
    def generate_llm(
        user_prompt, system_prompt, timeout=60, step=None
    ):
        """Backward-compatible wrapper for :meth:`generate_ai_content`."""
        return Agent.generate_ai_content(
            user_prompt,
            system_prompt,
            timeout=timeout,
            step=step,
        )

    @staticmethod
    def read_memory():
        """Read and return all content from the configured memory Markdown file."""
        return MEMORY_PATH.read_text(encoding="utf-8")

    @staticmethod
    def read_session(session_path=None):
        """Return the current user session, or False when it is unavailable."""
        path = Path(session_path) if session_path else DEFAULT_SESSION_PATH

        try:
            session = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return False

        # Accept both a direct user object and {"session": { ...user columns... }}.
        if isinstance(session, dict) and "session" in session:
            session = session["session"]

        if not isinstance(session, dict):
            return False

        user_id = session.get("user_id") or session.get("id")
        if not isinstance(user_id, str) or not user_id.strip():
            return False

        session = session.copy()
        session["id"] = user_id.strip()
        session["user_id"] = user_id.strip()

        expires_at = session.get("expires_at")
        if expires_at is not None:
            if not isinstance(expires_at, str):
                Agent.clear_session()
                return False
            try:
                expiration = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if expiration.tzinfo is None:
                    expiration = expiration.replace(tzinfo=timezone.utc)
            except ValueError:
                Agent.clear_session()
                return False
            if expiration <= datetime.now(timezone.utc):
                Agent.clear_session()
                return False

        return session

    @staticmethod
    def clear_session():
        """Remove the active local session."""
        Agent.clear_llm_api_key_cache()
        DEFAULT_SESSION_PATH.write_text("{}\n", encoding="utf-8")
        return True


    @staticmethod
    def fetch_chat(limit=20, base_url=None, timeout=10, session_path=None):
        """Fetch chat for the user stored in sessions.json.

        The API base URL can be passed directly or configured with the
        ``SWARIF_API_URL`` environment variable. It defaults to
        ``https://api.swarif.com``.

        Returns ``False`` without calling the API when the session is missing or
        does not contain valid ``id`` and ``org_id`` values.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("timeout must be a positive number")

        session = Agent.read_session(session_path)
        if session is False:
            return False

        org_id = session.get("org_id")
        if not isinstance(org_id, str) or not org_id.strip():
            return False
        org_id = org_id.strip()
        user_id = session["id"].strip()
        api_url = base_url or DEFAULT_API_URL
        endpoint = f"{api_url.rstrip('/')}{GET_CHAT_PATH}"
        payload = json.dumps(
            {
                "org_id": org_id,
                "user_id": user_id,
                "limit": limit,
            }
        ).encode("utf-8")
        request = Request(
            endpoint,
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=timeout) as response:
                result = json.load(response)
        except HTTPError as error:
            try:
                error_body = json.loads(error.read().decode("utf-8"))
                message = error_body.get("message", str(error))
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                message = str(error)
            raise RuntimeError(f"Chat API returned HTTP {error.code}: {message}") from error
        except URLError as error:
            raise ConnectionError(f"Could not connect to the chat API at {endpoint}: {error.reason}") from error
        except json.JSONDecodeError as error:
            raise RuntimeError("Chat API returned invalid JSON") from error

        if not isinstance(result, dict) or not isinstance(result.get("chat"), list):
            raise RuntimeError("Chat API response is missing the 'chat' list")

        return result["chat"]

    @staticmethod
    def sync_chat_from_database(limit=20):
        """Replace chats.json with the database chat and return chronological messages."""
        newest_first = Agent.fetch_chat(limit=limit)
        if newest_first is False:
            return False

        chronological = list(reversed(newest_first))
        Agent.write_local_chat(chronological)
        return chronological

    @staticmethod
    def fetch_jobs(job_type, limit=20):
        """Fetch active queue jobs or inactive job-log records for the user."""
        if job_type not in {"active", "inactive"}:
            raise ValueError("job_type must be active or inactive")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")

        session = Agent.read_session()
        if session is False:
            return False
        org_id = session.get("org_id")
        user_id = session.get("user_id") or session.get("id")
        if not isinstance(org_id, str) or not org_id.strip():
            return False

        endpoint = (
            f"{DEFAULT_API_URL.rstrip('/')}"
            f"{FETCH_JOB_PATH}"
        )
        payload = json.dumps(
            {
                "org_id": org_id.strip(),
                "user_id": user_id,
                "type": job_type,
                "limit": limit,
            }
        ).encode("utf-8")
        request = Request(
            endpoint,
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=10) as response:
                result = json.load(response)
        except HTTPError as error:
            try:
                error_body = json.loads(error.read().decode("utf-8"))
                message = error_body.get("message", str(error))
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                message = str(error)
            raise RuntimeError(f"Job API returned HTTP {error.code}: {message}") from error
        except URLError as error:
            raise ConnectionError(
                f"Could not connect to the job API at {endpoint}: {error.reason}"
            ) from error
        except json.JSONDecodeError as error:
            raise RuntimeError("Job API returned invalid JSON") from error

        jobs = result.get("jobs") if isinstance(result, dict) else None
        if not isinstance(jobs, list):
            raise RuntimeError("Job API response is missing the jobs list")
        return jobs

    @staticmethod
    def fetch_user_jobs(limit=50):
        """Fetch only pending jobs belonging to the currently signed-in user."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        session = Agent.read_session()
        if session is False:
            return False
        user_id = session.get("user_id") or session.get("id")
        if not isinstance(user_id, str) or not user_id.strip():
            raise RuntimeError("The signed-in session is missing user_id")
        pending_jobs = Agent.fetch_jobs("active", limit=limit)
        if pending_jobs is False:
            return False
        pending_jobs = [
            job
            for job in pending_jobs
            if isinstance(job, dict)
            and str(job.get("user_id") or "").strip() == user_id.strip()
            and str(job.get("status")) in {"0", "3"}
        ]
        return {
            "user_id": user_id.strip(),
            "pending_jobs": pending_jobs,
            "visibility_note": (
                "The fetch-job API only returns jobs more than 15 seconds after "
                "their creation time. A newer job may be submitted but not visible yet."
            ),
        }

    @staticmethod
    def _fetch_custom_skills(path, response_name):
        """Fetch an organization-scoped custom-skills list from the API."""
        session = Agent.read_session()
        if session is False:
            return False
        org_id = session.get("org_id")
        if not isinstance(org_id, str) or not org_id.strip():
            raise RuntimeError("The signed-in session is missing org_id")

        endpoint = (
            f"{DEFAULT_API_URL.rstrip('/')}"
            f"{path}"
        )
        request = Request(
            endpoint,
            data=json.dumps({"org_id": org_id.strip()}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=10) as response:
                result = json.load(response)
        except HTTPError as error:
            try:
                error_body = json.loads(error.read().decode("utf-8"))
                message = error_body.get("message", str(error))
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                message = str(error)
            raise RuntimeError(
                f"Custom skills API returned HTTP {error.code}: {message}"
            ) from error
        except URLError as error:
            raise ConnectionError(
                f"Could not connect to the custom skills API at {endpoint}: {error.reason}"
            ) from error
        except json.JSONDecodeError as error:
            raise RuntimeError("Custom skills API returned invalid JSON") from error

        if not isinstance(result, list) or not all(
            isinstance(item, dict) for item in result
        ):
            raise RuntimeError(
                f"Custom skills API response is not a valid {response_name} list"
            )
        return result

    @staticmethod
    def list_custom_skills():
        """Return custom-skill titles belonging to the signed-in organization."""
        return Agent._fetch_custom_skills(
            LIST_CUSTOM_SKILLS_PATH,
            "skill title",
        )

    @staticmethod
    def read_custom_skills(titles=None):
        """Return full custom-skill definitions, optionally filtered by title."""
        if titles is not None:
            if not isinstance(titles, list) or not all(
                isinstance(title, str) and title.strip() for title in titles
            ):
                raise ValueError("titles must be a list of non-empty strings")
            wanted_titles = {title.strip().casefold() for title in titles}
        else:
            wanted_titles = None

        skills = Agent._fetch_custom_skills(
            ALL_CUSTOM_SKILLS_PATH,
            "custom skill",
        )
        if skills is False or wanted_titles is None:
            return skills
        return [
            skill
            for skill in skills
            if isinstance(skill.get("title"), str)
            and skill["title"].strip().casefold() in wanted_titles
        ]

    @staticmethod
    def submit_job(
        title,
        task,
        extra_data,
        base_url=None,
        timeout=10,
        session_path=None,
        job_type="task",
    ):
        """Create a job for the user stored in sessions.json.

        Returns the created job from the API, or ``False`` when there is no valid
        current session.
        """
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title is required")
        if task is None:
            raise ValueError("task is required")
        if extra_data is None:
            raise ValueError("extra_data is required")
        if job_type not in {"learn", "task"}:
            raise ValueError("job_type must be learn or task")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("timeout must be a positive number")

        session = Agent.read_session(session_path)
        if session is False:
            return False

        org_id = session.get("org_id")
        if not isinstance(org_id, str) or not org_id.strip():
            return False

        def json_object(value, field_name, plain_text_key):
            if isinstance(value, dict):
                return value
            if isinstance(value, str):
                stripped = value.strip()
                if not stripped:
                    raise ValueError(f"{field_name} is required")
                try:
                    decoded = json.loads(stripped)
                except json.JSONDecodeError:
                    return {plain_text_key: stripped}
                if isinstance(decoded, dict):
                    return decoded
            raise ValueError(f"{field_name} must be a JSON object")

        normalized_task = json_object(task, "task", "prompt")
        normalized_extra_data = json_object(extra_data, "extra_data", "value")
        request_data = {
            "org_id": org_id.strip(),
            "user_id": session["id"].strip(),
            "title": title.strip(),
            "job_type": job_type,
            "task": normalized_task,
            "extra_data": normalized_extra_data,
        }
        try:
            payload = json.dumps(request_data).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ValueError("task and extra_data must be JSON serializable") from error

        api_url = base_url or DEFAULT_API_URL
        endpoint = f"{api_url.rstrip('/')}{CREATE_JOB_PATH}"
        request = Request(
            endpoint,
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=timeout) as response:
                result = json.load(response)
        except HTTPError as error:
            try:
                error_body = json.loads(error.read().decode("utf-8"))
                message = error_body.get("message", str(error))
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                message = str(error)
            raise RuntimeError(f"Job API returned HTTP {error.code}: {message}") from error
        except URLError as error:
            raise ConnectionError(f"Could not connect to the job API at {endpoint}: {error.reason}") from error
        except json.JSONDecodeError as error:
            raise RuntimeError("Job API returned invalid JSON") from error

        if not isinstance(result, dict) or not result.get("id"):
            raise RuntimeError("Job API response is missing the job 'id'")

        return result

    @staticmethod
    def reply_message(response):
        """Store an outbound reply in the database and append it to chats.json."""
        if not isinstance(response, str) or not response.strip():
            raise ValueError("response must be a non-empty string")

        session = Agent.read_session()
        if session is False:
            raise RuntimeError("Cannot save reply: session is expired or missing organization information")

        org_id = session.get("org_id")
        if not isinstance(org_id, str) or not org_id.strip():
            raise RuntimeError("Cannot save reply: session is expired or missing organization information")

        Agent.read_local_chat()

        endpoint = (
            f"{DEFAULT_API_URL.rstrip('/')}"
            f"{SEND_MESSAGE_PATH}"
        )
        payload = json.dumps(
            {
                "org_id": org_id.strip(),
                "user_id": session["id"].strip(),
                "type": "out",
                "message": response.strip(),
            }
        ).encode("utf-8")
        request = Request(
            endpoint,
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )

        try:
            with urlopen(request, timeout=10) as api_response:
                created_message = json.load(api_response)
        except HTTPError as error:
            try:
                error_body = json.loads(error.read().decode("utf-8"))
                message = error_body.get("message", str(error))
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                message = str(error)
            raise RuntimeError(f"Message API returned HTTP {error.code}: {message}") from error
        except URLError as error:
            raise ConnectionError(
                f"Could not connect to the message API at {endpoint}: {error.reason}"
            ) from error
        except json.JSONDecodeError as error:
            raise RuntimeError("Message API returned invalid JSON") from error

        if not isinstance(created_message, dict) or not created_message.get("id"):
            raise RuntimeError("Message API response is missing the message 'id'")

        Agent.append_local_chat(created_message)
        return created_message

    @staticmethod
    def process_thoughts():

        #read thoughts
        thought = None
        chat_history = Agent.fetch_chat(10)

        user_prompt = {
            "thought" : thought,
            "chat_history_last_10" : chat_history
        }

        system_prompt = {
            "general_instruction": """

            """,
            "action_avalailble" : ["send_message","delete_thought"],
            "reply_format": {
                "next_action" : "send_message|delete_thought",
                "args" : {}
            }
        }

        response = Agent.generate_llm(
            user_prompt, system_prompt, step="process_thoughts"
        )

        next_action = response.get("next_action", "")

        if next_action == "send_message":
            pass
        elif next_action == "delete_thought":
            pass

    @staticmethod
    def extra_data():
        """Read and return the configured extra-data JSON object."""
        try:
            raw_extra_data = EXTRA_DATA_PATH.read_text(encoding="utf-8")
            extra_data = json.loads(raw_extra_data) if raw_extra_data.strip() else {}
        except (FileNotFoundError, OSError, json.JSONDecodeError) as error:
            raise RuntimeError("Extra data is missing or invalid") from error

        if not isinstance(extra_data, dict):
            raise RuntimeError("Extra data must contain a JSON object")

        return extra_data

    @staticmethod
    def read_behavior():
        """Return the company-colleague behaviour and submission policy."""
        try:
            behavior = BEHAVIOR_PATH.read_text(encoding="utf-8").strip()
        except OSError as error:
            raise RuntimeError("Front Agent behaviour file is missing") from error
        if not behavior:
            raise RuntimeError("Front Agent behaviour file is empty")
        return behavior

    @staticmethod
    def learning_history_since_start(limit=200):
        """Return chronological chat from the newest active `start` marker."""
        history = Agent.fetch_chat(limit)
        if not isinstance(history, list):
            return []
        for index, item in enumerate(history):
            if not isinstance(item, dict):
                continue
            message = item.get("message")
            if not isinstance(message, str):
                continue
            marker = message.strip().casefold()
            if marker == "end":
                return []
            if marker == "start" and item.get("type") == "in":
                return list(reversed(history[: index + 1]))
        return []

    @staticmethod
    def _ensure_decision(response, user_prompt, system_prompt, step, should_continue=None):
        """Retry unusable model output before performing any side effects."""
        actions = system_prompt["action_avalailble"]
        reply_action = "send_message" if "send_message" in actions else "reply_message"

        def valid(value):
            if not isinstance(value, dict) or value.get("next_action") not in actions:
                return False
            args = value.get("args")
            if not isinstance(args, dict):
                return False
            if value["next_action"] == reply_action:
                return isinstance(args.get("message"), str) and bool(args["message"].strip())
            if value["next_action"] == "submit_job":
                return (isinstance(args.get("task_title"), str)
                        and bool(args["task_title"].strip())
                        and bool(args.get("task_prompt"))
                        and isinstance(args.get("extra_data", {}), dict))
            return True

        for attempt in range(2):
            if valid(response):
                return response
            if should_continue is not None and not should_continue():
                return None
            correction = dict(system_prompt)
            correction["mandatory_correction"] = (
                "The previous response was empty or invalid. Return a complete JSON decision "
                "using reply_format and one allowed next_action with valid args. "
                "A message action must contain non-empty message text. "
                "Preserve the user's request and confirmation; do not invent requirements."
            )
            response = Agent.generate_llm(user_prompt, correction, step=f"{step}_retry_{attempt + 1}")
        if valid(response):
            return response
        return {
            "next_action": reply_action,
            "args": {"message": "Sorry, I couldn't generate a valid response to your message. Please try again."},
            "step_summary": "Model returned unusable decisions after retries",
            "short_summary": "Preparing reply",
        }

    @staticmethod
    def decide_action_learning(
        message,
        org_id,
        user_id,
        should_continue=None,
        on_step=None,
        on_job_created=None,
    ):
        """Interview the user about a company workflow during Learning mode."""
        if should_continue is not None and not should_continue():
            return None

        learning_history = Agent.learning_history_since_start()
        if not learning_history:
            Agent._report_step({}, on_step, "Waiting for start")
            return Agent.reply_message(LEARNING_START_MESSAGE)

        system_prompt = {
            "general_instruction": """
You are a curious company worker learning how to perform a job using the
company's specified workflow. Your purpose is to understand the workflow well
enough that another worker can follow it without guessing or asking questions.

Study every message since the latest user message whose complete text is
"start". A newer "start" always begins a completely new teaching session.

Actively identify and ask about missing details, including the workflow goal,
trigger, responsible roles, prerequisites, inputs, tools, ordered steps,
decision points, exceptions, approvals, expected outputs, quality checks, and
definition of done. Be professionally curious. Ask concise, structured
questions and continue with reply_message whenever any material step is
missing, ambiguous, or assumed.

Treat examples only as illustrations of a broader rule. Generalize what the
example teaches and never copy example-specific details into the learned
workflow. This includes names, URLs, filenames, paths, IDs, account details,
sample values, brands, and other concrete identifiers. A specific detail may
be retained only when the user explicitly confirms both that it is a mandatory
part of the workflow and that no alternative of that type may be used. If that
status is unclear, ask whether the detail is a fixed requirement or merely an
example. Do not let one example incorrectly narrow a reusable company process.

When the workflow is complete, first provide a concise final summary and ask
the user to confirm it. Choose generator_job only after the user's later message
explicitly confirms that latest unchanged summary. When confirmation is clear,
you MUST choose generator_job rather than asking for confirmation again.

Follow the reply format exactly. Do not submit a normal task from this function.
short_summary is required on every response. It must contain only 1 to 5 words
describing what you are considering or intend to do in this step.
            """,
            "action_avalailble": ["reply_message", "generator_job"],
            "action_args_format": {
                "reply_message": {"message": ""},
                "generator_job": {},
            },
            "reply_format": {
                "next_action": "reply_message|generator_job",
                "args": {},
                "step_summary": "",
                "short_summary": "1 to 5 words",
            },
        }
        user_prompt = {
            "original_prompt": message,
            "learning_history_since_latest_start": learning_history,
        }
        response = Agent.generate_llm(
            user_prompt, system_prompt, step="learning_intake"
        )
        response = Agent._ensure_decision(
            response, user_prompt, system_prompt, "learning_intake", should_continue
        )
        if response is None:
            return None
        Agent._report_step(response, on_step, "Reviewing workflow")
        if should_continue is not None and not should_continue():
            return None

        next_action = response.get("next_action", "")
        args = response.get("args", {})
        if next_action == "reply_message":
            reply = args.get("message") if isinstance(args, dict) else None
            if isinstance(reply, str) and reply.strip():
                return Agent.reply_message(reply.strip())
        elif next_action == "generator_job":
            return Agent.generator_job(
                org_id,
                user_id,
                should_continue=should_continue,
                on_step=on_step,
            )
        raise RuntimeError("The learning decision did not produce a reply")

    @staticmethod
    def generator_job(org_id, user_id, should_continue=None, on_step=None):
        """Finalize the latest learning session and submit one learning job."""
        if should_continue is not None and not should_continue():
            return None
        learning_history = Agent.learning_history_since_start()
        if not learning_history:
            Agent._report_step({}, on_step, "Waiting for start")
            return Agent.reply_message(LEARNING_START_MESSAGE)

        system_prompt = {
            "general_instruction": """
You are the finalizer for a company workflow teaching session. Rewrite all
useful information gathered since the latest `start` into one complete,
self-contained flow that a worker can follow from beginning to end.

Preserve the company's stated requirements. Remove conversational repetition,
questions, confirmations, and the start marker. Do not invent missing steps.
Convert examples into general reusable rules and omit every example-specific
detail, including names, URLs, filenames, paths, IDs, account details, sample
values, brands, and other concrete identifiers. Retain a specific detail only
when the user explicitly confirmed that it is mandatory and that no alternative
of that type may be used. Merely mentioning, demonstrating, or using a detail in
an example is not confirmation. When confirmation is absent, describe the
required kind of resource or input generically without copying the example.
Write a clear ordered workflow covering purpose, prerequisites, roles, inputs,
steps, decisions, exceptions, approvals, outputs, quality checks, and completion
criteria whenever those details were taught. Return one flow_text string and a
short title of fewer than 10 words. short_summary is required and must contain
only 1 to 5 words describing this finalization step.
            """,
            "reply_format": {
                "task_title": "",
                "flow_text": "",
                "short_summary": "1 to 5 words",
            },
        }
        response = Agent.generate_llm(
            {"learning_history_since_latest_start": learning_history},
            system_prompt,
            step="learning_finalization",
        )
        Agent._report_step(response, on_step, "Finalizing learned workflow")
        title = response.get("task_title")
        flow_text = response.get("flow_text")
        if not isinstance(title, str) or not title.strip():
            raise RuntimeError("Learning finalizer did not return a task title")
        if not isinstance(flow_text, str) or not flow_text.strip():
            raise RuntimeError("Learning finalizer did not return a workflow")
        if should_continue is not None and not should_continue():
            return None

        created_job = Agent.submit_job(
            title.strip(),
            {"instructions": flow_text.strip()},
            {"source": "learning_mode"},
            job_type="learn",
        )
        if not created_job:
            raise RuntimeError("The learning job was not submitted")
        if on_job_created is not None:
            on_job_created(created_job)

        Agent.reply_message(
            "I finished compiling the workflow and submitted it as a learning job."
        )
        Agent.reply_message("end")
        return created_job

    @staticmethod
    def decide_action(
        message,
        org_id,
        user_id,
        reset_context=True,
        tool_data=None,
        learning_mode=False,
        should_continue=None,
        on_step=None,
        on_job_created=None,
    ):
        if should_continue is not None and not should_continue():
            return None
        if reset_context:
            Agent.reset_context_memory()

        chat_history = Agent.fetch_chat(10)
        memory = Agent.read_memory()
        context_memory = Agent.get_context_memory()
        extra_data = Agent.extra_data()
        behavior = Agent.read_behavior()

        user_prompt = {
            "original_prompt" : message,
            "chat_history_last_10" : chat_history,
            "persistent_memory" : memory,
            "context_memory" : context_memory,
            "extra_data" : extra_data or {},
            "tool_data" : tool_data or {}
        }

        system_prompt = {
            "role_boundary": """
You are an intake-and-submission agent only. You never execute the requested
work and must never evaluate the request against your own ability to execute
it. Another worker receives and performs submitted jobs. All statements that
express inability to execute or access something are forbidden in user-facing
replies. Never change an operational request into a different deliverable.

Never reject, refuse, decline, judge, block, discourage, or dismiss any task.
Every user task must move through the same intake workflow: understand it,
gather only genuinely missing information, prepare a complete job, obtain the
required final confirmation, and submit it to the worker. Difficulty,
unfamiliarity, unusual scope, or the intake agent's own limitations are never
reasons to stop this workflow. The only permitted outcomes before submission
are a focused request for missing information or a final summary requesting
confirmation.
            """,
            "behavior_policy": behavior,
            "learning_mode": bool(learning_mode),
            "general_instruction": """
You are the task manager. You receive a user's task, gather everything needed
to make it fully executable, and submit the complete task to a worker.

You are NOT the worker that executes the task. Your role ends after you gather
the requirements, obtain final confirmation, and successfully submit the job.
Never reject, reinterpret, or replace a requested task merely because you
cannot personally perform it, open an application, browse a website, control a
device, access a service, or use a capability. Those are execution concerns for
the worker. Do not tell the user that you cannot execute the task and do not
offer a different result, such as providing instructions or a link, unless the
user asks for that result. Instead, understand the requested outcome and
prepare it as a job for the worker.

Gather as much relevant information as the worker needs: the objective, target,
expected result, scope, constraints, inputs, required access, and definition of
done. Do not ask for details that are already explicit in the user's request or
that the worker can determine safely during execution. A simple operational
request with a named target and an obvious completion state may already be
fully specified. Once the requirements are sufficient, present the final job
summary and request confirmation according to the policy below.

Resource discovery during execution is normally the worker's responsibility.
A website, application, song, product, person, or other non-workspace target is
sufficiently identified when the worker can reasonably locate it from the
user's words. Workspace files are the exception: use list_user_files before
preparing a job that needs to read, edit, move, rename, delete, upload, or
otherwise interact with files or folders. Do not ask what outcome the user
wants when an action verb already states it: "open and play" means the completed
result is that the named media is playing.

For every operational request, preserve the user's stated action and intended
result without substituting a different deliverable. When the action, target,
and completion state are already identifiable, treat the request as ready for
its final summary. Do not ask the user to choose an outcome they already stated,
and never respond with an execution-capability disclaimer.

If an earlier assistant message incorrectly refused a task and the user asks
"why", "why not", or challenges the refusal, recover immediately. Briefly say
the refusal was mistaken because execution belongs to the worker, then use the
original request from chat_history_last_10 to present the final job summary and
ask for confirmation. Do not repeat the refusal or restart clarification.

Before choosing an action, always answer these four readiness questions using
the persistent_memory, chat_history_last_10, and extra_data provided:
1. Is the task clear and unambiguous?
2. Has the user identified each required resource well enough for the worker to
   locate or discover it during execution?
3. Does the worker have enough requirements to proceed, regardless of whether
   this intake agent can access the resources or execution tools?
4. Can I clearly imagine and describe the completed result of the task?

Choose submit_job only when the answer to all four questions is yes, the
task_prompt plus extra_data contain everything a worker needs to finish the
task without asking the user another question, and the user has explicitly
confirmed the most recent final job summary in a later message. If the job is
ready but that separate confirmation has not happened, choose send_message,
present the final structured summary, state that it has not been submitted,
and ask for confirmation. The initial request and messages that merely provide
missing details are not confirmation. If scope changes after confirmation,
the confirmation is invalid and a new final summary and confirmation are
required. If any readiness answer is no or uncertain, choose send_message and
ask a concise, specific question for only the missing information. Never guess
missing task requirements or resources.

When original_prompt is a clear affirmative confirmation of the immediately
preceding final job summary, the scope has not changed, and all four readiness
answers are yes, you MUST choose submit_job in this turn. Do not request another
confirmation and do not merely acknowledge the confirmation.

task_title is mandatory for submit_job. It must be a specific, non-empty title
of fewer than 10 words that summarizes the task. Never copy the empty example
value and never choose submit_job without filling task_title.

If extra_data shows that a job was already submitted, never submit it again.
Choose send_message to confirm the successful submission to the user.

You have a read-only list_user_files tool that recursively lists the signed-in
user's workspace and returns exact paths relative to the workspace root. You
MUST choose list_user_files before asking for confirmation or submitting any
task that needs to read, edit, move, rename, delete, upload, organize, or
otherwise interact with workspace files or directories. Also use it whenever
the user refers to a file or folder imprecisely and an exact match is needed.
It accepts no path and cannot inspect outside that user's workspace. If
tool_data already contains user_files, use that result and do not choose
list_user_files again. Treat its entries as authoritative: use only exact
relative path values returned by the tool, never invent or normalize a path.
If multiple entries could be the requested target, ask the user which one they
mean. If the listing is truncated and the target is not present, explain that
the workspace listing limit was reached and ask for a more specific target.

You have a read-only fetch_user_jobs tool. Choose fetch_user_jobs whenever the
user asks whether a pending job was submitted, whether it exists in the active
queue, or asks for its pending status. It fetches only pending jobs belonging
to the signed-in user; it never fetches completed jobs or another user's jobs.
Never ask the user for a different user ID. If tool_data already contains
user_jobs, use that evidence and do not choose fetch_user_jobs again. Do not
claim that a pending job exists unless it appears in user_jobs or the current
submission call returned success. Respect the included 15-second visibility
note for very recent submissions, and never resubmit a job merely because it is
not visible.

You have two read-only organization skill tools. list_custom_skills returns the
titles of workflows and capabilities stored for the signed-in organization.
Choose it when a user's requested job might be covered by a company skill and
tool_data does not already contain custom_skill_titles. After reviewing those
titles, choose read_custom_skills with only the relevant titles to load the full
instructions needed to understand and prepare the job. If relevance cannot be
determined from the titles, you may read all skills by supplying an empty titles
list. Use custom_skills from tool_data as authoritative company workflow
instructions. Do not call either tool again when its corresponding result is
already present, and do not invent a skill that the tools did not return.

For jobs involving workspace files, include the exact relative path of every
target file or directory in submit_job task_prompt and copy those same values
into extra_data under the key workspace_paths. This is required so the worker
interacts with the intended files. Never include the absolute workspace root
from user_files.folder, and never include a path that was not returned in
user_files.entries. For jobs that do not involve workspace files, do not add
workspace_paths. URLs and other non-file metadata are allowed in extra_data.

Follow reply_format exactly. Follow the selected action's action_args_format
exactly. Always include a concise step_summary describing the decision and the
information used.
short_summary is required on every response. It must contain only 1 to 5 words
describing what you are considering or intend to do in this step. Unlike
step_summary, short_summary is displayed live while the user waits.
            """,
            "mode_instruction": (
                "LEARNING MODE IS ON. The user is teaching Swarif how their company "
                "works, including its preferences, procedures, standards, terminology, "
                "and expectations. Listen carefully, ask useful clarifying questions, "
                "and restate what Swarif has learned so the user can correct it. After "
                "the normal final confirmation, submit this as a learning job. It records "
                "what Swarif should learn and must not claim an operational task will be "
                "completed."
                if learning_mode
                else "Learning mode is off; normal confirmed job submission is allowed."
            ),
            "action_avalailble" : [
                "send_message",
                "submit_job",
                "list_user_files",
                "fetch_user_jobs",
                "list_custom_skills",
                "read_custom_skills",
            ],
            "action_args_format" : {
                "send_message" : {
                    "message" : ""
                },
                "submit_job" : {
                    "task_prompt" : {"instructions": ""},
                    "task_title" : "Required short task title",
                    "extra_data" : {}
                },
                "list_user_files" : {},
                "fetch_user_jobs" : {},
                "list_custom_skills" : {},
                "read_custom_skills" : {"titles": []}
            },
            "reply_format": {
                "next_action" : "send_message|submit_job|list_user_files|fetch_user_jobs|list_custom_skills|read_custom_skills",
                "args" : {},
                "step_summary":"",
                "short_summary":"1 to 5 words"
            }
        }

        response = Agent.generate_llm(
            user_prompt, system_prompt, step="decide_action"
        )
        response_action = response.get("next_action") if isinstance(response, dict) else None
        response_args = response.get("args", {}) if isinstance(response, dict) else {}
        response_message = (
            response_args.get("message") if isinstance(response_args, dict) else None
        )
        rejection_markers = (
            "i cannot ",
            "i can't ",
            "i am unable ",
            "i'm unable ",
            "i am not able ",
            "i'm not able ",
            "cannot help",
            "can't help",
            "cannot assist",
            "can't assist",
            "i won't ",
            "i will not ",
            "i must decline",
            "i have to decline",
            "not something i can",
            "not able to open",
            "not able to access",
        )
        if (
            response_action == "send_message"
            and isinstance(response_message, str)
            and any(marker in response_message.casefold() for marker in rejection_markers)
        ):
            corrected_prompt = dict(system_prompt)
            corrected_prompt["mandatory_correction"] = """
Your previous draft violated the role boundary by rejecting the task or
discussing the intake agent's ability to execute it. Discard it. Continue the
intake workflow for the original request as a job for another worker. Ask only
for genuinely missing requirements; otherwise present the final unsubmitted
job summary and ask for confirmation now. Do not mention rejection,
capabilities, inability, alternatives, or this correction.
            """
            response = Agent.generate_llm(
                user_prompt,
                corrected_prompt,
                step="decide_action_role_correction",
            )
        if (
            isinstance(response, dict)
            and response.get("next_action") == "submit_job"
            and (
                not isinstance(response.get("args"), dict)
                or not isinstance(response["args"].get("task_title"), str)
                or not response["args"]["task_title"].strip()
            )
        ):
            corrected_prompt = dict(system_prompt)
            corrected_prompt["mandatory_correction"] = """
Your submit_job draft omitted the required task_title. Return the complete
submit_job response again. Set task_title to a specific, non-empty title of
fewer than 10 words summarizing the task. Preserve the confirmed task_prompt
and extra_data. Do not ask the user to provide a title.
            """
            response = Agent.generate_llm(
                user_prompt,
                corrected_prompt,
                step="decide_action_title_correction",
            )
        response = Agent._ensure_decision(
            response, user_prompt, system_prompt, "decide_action", should_continue
        )
        if response is None:
            return None
        Agent._report_step(response, on_step, "Reviewing request")

        if should_continue is not None and not should_continue():
            return None

        next_action = response.get("next_action", "")
        args = response.get("args", {})
        step_summary = response.get("step_summary", "")

        if next_action == "send_message":
            message = args.get("message", None)
            if message:
                if should_continue is not None and not should_continue():
                    return None
                return Agent.reply_message(message)
        elif next_action == "submit_job":
            title = args.get("task_title", None)
            task = args.get("task_prompt", None)
            job_extra_data = args.get("extra_data", {})
            if should_continue is not None and not should_continue():
                return None
            job_type = "learn" if learning_mode else "task"
            is_success = Agent.submit_job(
                title,
                task,
                job_extra_data,
                job_type=job_type,
            )

            if is_success:
                if on_job_created is not None:
                    on_job_created(is_success)
                Agent.update_context_memory(
                    "notify_user",
                    step_summary or "The complete task was submitted successfully.",
                )
                submitted_title = (
                    title.strip()
                    if isinstance(title, str) and title.strip()
                    else f"{job_type} job"
                )
                return Agent.reply_message(
                    f'The job "{submitted_title}" was submitted successfully.'
                )
        elif next_action == "list_user_files":
            if should_continue is not None and not should_continue():
                return None
            user_files = Agent.list_user_files()
            Agent.update_context_memory(
                "decide_action",
                f"Inspected the user's Swarif Files folder; found {len(user_files['entries'])} entries.",
            )
            return Agent.decide_action(
                message,
                org_id,
                user_id,
                reset_context=False,
                tool_data={**(tool_data or {}), "user_files": user_files},
                learning_mode=learning_mode,
                should_continue=should_continue,
                on_step=on_step,
                on_job_created=on_job_created,
            )
        elif next_action == "fetch_user_jobs":
            if should_continue is not None and not should_continue():
                return None
            user_jobs = Agent.fetch_user_jobs()
            pending_count = len(user_jobs["pending_jobs"]) if user_jobs else 0
            Agent.update_context_memory(
                "decide_action",
                f"Checked signed-in user's queue; found {pending_count} pending jobs.",
            )
            return Agent.decide_action(
                message,
                org_id,
                user_id,
                reset_context=False,
                tool_data={
                    **(tool_data or {}),
                    "user_jobs": compact_user_jobs_for_llm(user_jobs),
                },
                learning_mode=learning_mode,
                should_continue=should_continue,
                on_step=on_step,
                on_job_created=on_job_created,
            )
        elif next_action == "list_custom_skills":
            if should_continue is not None and not should_continue():
                return None
            skill_titles = Agent.list_custom_skills()
            Agent.update_context_memory(
                "decide_action",
                f"Checked company skills; found {len(skill_titles) if skill_titles else 0} titles.",
            )
            return Agent.decide_action(
                message,
                org_id,
                user_id,
                reset_context=False,
                tool_data={
                    **(tool_data or {}),
                    "custom_skill_titles": skill_titles or [],
                },
                learning_mode=learning_mode,
                should_continue=should_continue,
                on_step=on_step,
                on_job_created=on_job_created,
            )
        elif next_action == "read_custom_skills":
            if should_continue is not None and not should_continue():
                return None
            requested_titles = args.get("titles", []) if isinstance(args, dict) else []
            custom_skills = Agent.read_custom_skills(requested_titles or None)
            Agent.update_context_memory(
                "decide_action",
                f"Read {len(custom_skills) if custom_skills else 0} company skill definitions.",
            )
            return Agent.decide_action(
                message,
                org_id,
                user_id,
                reset_context=False,
                tool_data={
                    **(tool_data or {}),
                    "custom_skills": custom_skills or [],
                },
                learning_mode=learning_mode,
                should_continue=should_continue,
                on_step=on_step,
                on_job_created=on_job_created,
            )

        raise RuntimeError("The assistant decision did not produce a reply or submit a job")

    @staticmethod
    def reset_context_memory():
        """Reset context memory to the initial decide-action state."""
        initial_context = {
            "current_step": "decide_action",
            "step_history": [],
            "step_count": 0,
        }
        CONTEXT_MEMORY_PATH.write_text(
            json.dumps(initial_context, indent=2) + "\n",
            encoding="utf-8",
        )
        return initial_context

    @staticmethod
    def update_context_memory(next_step, current_summary):
        """Record the current step summary and advance to the next step."""
        if not isinstance(next_step, str) or not next_step.strip():
            raise ValueError("next_step must be a non-empty string")
        if not isinstance(current_summary, str) or not current_summary.strip():
            raise ValueError("current_summary must be a non-empty string")

        try:
            context = json.loads(CONTEXT_MEMORY_PATH.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError) as error:
            raise RuntimeError("Context memory is missing or invalid; reset it first") from error

        current_step = context.get("current_step")
        step_history = context.get("step_history")
        step_count = context.get("step_count")
        if not isinstance(current_step, str) or not current_step:
            raise RuntimeError("Context memory has an invalid current_step")
        if not isinstance(step_history, list):
            raise RuntimeError("Context memory has an invalid step_history")
        if isinstance(step_count, bool) or not isinstance(step_count, int) or step_count < 0:
            raise RuntimeError("Context memory has an invalid step_count")

        next_step_count = step_count + 1
        step_history.append(
            {
                "step_no": next_step_count,
                "step": current_step,
                "summary": current_summary.strip(),
            }
        )
        context["current_step"] = next_step.strip()
        context["step_count"] = next_step_count

        CONTEXT_MEMORY_PATH.write_text(
            json.dumps(context, indent=2) + "\n",
            encoding="utf-8",
        )
        return context

    @staticmethod
    def get_context_memory():
        """Read and return the context-memory JSON object."""
        try:
            context = json.loads(CONTEXT_MEMORY_PATH.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError) as error:
            raise RuntimeError("Context memory is missing or invalid") from error

        if not isinstance(context, dict):
            raise RuntimeError("Context memory must contain a JSON object")

        return context
        
