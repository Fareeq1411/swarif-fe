import os
import json
import ipaddress
import platform
import socket
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from openai import OpenAI, OpenAIError
from error_logger import log_static_methods


load_dotenv(Path(__file__).with_name(".env"))

DEFAULT_API_URL = os.getenv("SWARIF_API_URL", "http://localhost:8080")
DEFAULT_DEEPSEEK_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEFAULT_DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
LOGIN_PATH = "/api/auth/login"
GET_USER_PATH = "/api/user/get-user"
GET_CHAT_PATH = "/api/client-app/get-chat"
SEND_MESSAGE_PATH = "/api/client-app/send-message"
FETCH_JOB_PATH = "/api/client-app/job/fetch-job"
CREATE_JOB_PATH = "/api/client-app/job/create-job"
DEFAULT_SESSION_PATH = Path(os.getenv("SESSION_PATH", "sessions.json"))
if not DEFAULT_SESSION_PATH.is_absolute():
    DEFAULT_SESSION_PATH = Path(__file__).parent / DEFAULT_SESSION_PATH
MEMORY_PATH = Path(os.getenv("MEMORY_PATH", "memory.md"))
if not MEMORY_PATH.is_absolute():
    MEMORY_PATH = Path(__file__).parent / MEMORY_PATH
CONTEXT_MEMORY_PATH = Path(os.getenv("CONTEXT_MEMORY_PATH", "context_memory.json"))
if not CONTEXT_MEMORY_PATH.is_absolute():
    CONTEXT_MEMORY_PATH = Path(__file__).parent / CONTEXT_MEMORY_PATH
CHAT_PATH = Path(os.getenv("CHAT_PATH", "chats.json"))
if not CHAT_PATH.is_absolute():
    CHAT_PATH = Path(__file__).parent / CHAT_PATH
EXTRA_DATA_PATH = Path(os.getenv("EXTRA_DATA_PATH", "extra_data.json"))
if not EXTRA_DATA_PATH.is_absolute():
    EXTRA_DATA_PATH = Path(__file__).parent / EXTRA_DATA_PATH
CHAT_FILE_LOCK = threading.RLock()
SESSION_FILE_LOCK = threading.RLock()



@log_static_methods
class Agent:
    @staticmethod
    def user_files_path(session_path=None):
        """Return the current user's platform-specific Swarif Files directory."""
        session = Agent.read_session(session_path)
        if session is False:
            raise RuntimeError("Your session has expired. Please sign in again.")

        user_id = session["user_id"]
        agent_ip = session.get("agent_ip")
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
        org_id = session.get("org_id") or os.getenv("SWARIF_ORG_ID")
        token = session.get("token")
        if not isinstance(org_id, str) or not org_id.strip():
            raise RuntimeError("SWARIF_ORG_ID is missing from .env")
        if not isinstance(token, str) or not token:
            raise RuntimeError("The saved session is missing its token")

        endpoint = (
            f"{os.getenv('SWARIF_API_URL', DEFAULT_API_URL).rstrip('/')}"
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
        if not isinstance(email, str) or not email.strip():
            raise ValueError("email must be a non-empty string")
        if not isinstance(password, str) or not password:
            raise ValueError("password must be a non-empty string")
        if not isinstance(remember_me, bool):
            raise ValueError("remember_me must be a boolean")

        endpoint = (
            f"{os.getenv('SWARIF_API_URL', DEFAULT_API_URL).rstrip('/')}"
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

        org_id = login_result.get("org_id") or os.getenv("SWARIF_ORG_ID")
        if not isinstance(org_id, str) or not org_id.strip():
            raise RuntimeError("SWARIF_ORG_ID is missing from .env")

        user_endpoint = (
            f"{os.getenv('SWARIF_API_URL', DEFAULT_API_URL).rstrip('/')}"
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
    def generate_llm(user_prompt, system_prompt, model=None, timeout=60, client=None):
        """Generate and return a JSON dictionary using DeepSeek."""
        def prompt_text(prompt, name):
            if isinstance(prompt, str):
                if not prompt.strip():
                    raise ValueError(f"{name} must not be empty")
                return prompt.strip()
            try:
                return json.dumps(prompt, ensure_ascii=False)
            except (TypeError, ValueError) as error:
                raise ValueError(f"{name} must be a string or JSON serializable") from error

        user_content = prompt_text(user_prompt, "user_prompt")
        system_content = prompt_text(system_prompt, "system_prompt")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("timeout must be a positive number")

        request_messages = [
            {
                "role": "system",
                "content": f"{system_content}\n\nReturn only a valid JSON object.",
            },
            {"role": "user", "content": user_content},
        ]

        api_key = os.getenv("DEEPSEEK_API_KEY")
        if client is None:
            if not api_key:
                raise RuntimeError("DEEPSEEK_API_KEY is missing from .env")
            client = OpenAI(
                api_key=api_key,
                base_url=os.getenv("DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_URL),
                timeout=timeout,
            )

        try:
            response = client.chat.completions.create(
                model=model or os.getenv("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL),
                messages=request_messages,
                response_format={"type": "json_object"},
                stream=False,
            )
        except OpenAIError as error:
            raise RuntimeError(f"DeepSeek API request failed: {error}") from error

        if not response.choices or not response.choices[0].message.content:
            raise RuntimeError("DeepSeek API returned an empty response")

        try:
            result = json.loads(response.choices[0].message.content)
        except json.JSONDecodeError as error:
            raise RuntimeError("DeepSeek API returned invalid JSON") from error

        if not isinstance(result, dict):
            raise RuntimeError("DeepSeek API response must be a JSON object")

        return result

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
        DEFAULT_SESSION_PATH.write_text("{}\n", encoding="utf-8")
        return True


    @staticmethod
    def fetch_chat(limit=20, base_url=None, timeout=10, session_path=None):
        """Fetch chat for the user stored in sessions.json.

        The API base URL can be passed directly or configured with the
        ``SWARIF_API_URL`` environment variable. It defaults to the local Spring
        Boot address (``http://localhost:8080``).

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
        api_url = base_url or os.getenv("SWARIF_API_URL", DEFAULT_API_URL)
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
            f"{os.getenv('SWARIF_API_URL', DEFAULT_API_URL).rstrip('/')}"
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
    def submit_job(title, task, extra_data, base_url=None, timeout=10, session_path=None):
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
            "task": normalized_task,
            "extra_data": normalized_extra_data,
        }
        try:
            payload = json.dumps(request_data).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ValueError("task and extra_data must be JSON serializable") from error

        api_url = base_url or os.getenv("SWARIF_API_URL", DEFAULT_API_URL)
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
            return False

        org_id = session.get("org_id")
        if not isinstance(org_id, str) or not org_id.strip():
            return False

        Agent.read_local_chat()

        endpoint = (
            f"{os.getenv('SWARIF_API_URL', DEFAULT_API_URL).rstrip('/')}"
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

        response = Agent.generate_llm(user_prompt, system_prompt)

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
    def decide_action(
        message,
        org_id,
        user_id,
        reset_context=True,
        tool_data=None,
        should_continue=None,
    ):
        if should_continue is not None and not should_continue():
            return None
        if reset_context:
            Agent.reset_context_memory()

        chat_history = Agent.fetch_chat(10)
        memory = Agent.read_memory()
        context_memory = Agent.get_context_memory()
        extra_data = Agent.extra_data()

        user_prompt = {
            "original_prompt" : message,
            "chat_history_last_10" : chat_history,
            "persistent_memory" : memory,
            "context_memory" : context_memory,
            "extra_data" : extra_data or {},
            "tool_data" : tool_data or {}
        }

        system_prompt = {
            "general_instruction": """
You are the task manager. You receive a user's task, gather everything needed
to make it fully executable, and submit the complete task to a worker.

Before choosing an action, always answer these four readiness questions using
the persistent_memory, chat_history_last_10, and extra_data provided:
1. Is the task clear and unambiguous?
2. Do I know where to find every resource needed to perform the task?
3. Are all required resources and details sufficient and accessible?
4. Can I clearly imagine and describe the completed result of the task?

Choose submit_job only when the answer to all four questions is yes and the
task_prompt plus extra_data contain everything a worker needs to finish the
task without asking the user another question. If any answer is no or
uncertain, choose send_message and ask a concise, specific question for only
the missing information. Never guess missing task requirements or resources.

task_title must be short less than 10 words.

If extra_data shows that a job was already submitted, never submit it again.
Choose send_message to confirm the successful submission to the user.

You have a read-only list_user_files tool. Choose list_user_files when knowing
which files or folders exist in the current user's Swarif Files directory is
needed to answer or prepare the task. It accepts no path and cannot inspect
outside that user's folder. If tool_data already contains user_files, use that
result and do not choose list_user_files again.

Never include a file name, file path, folder path, directory name, or filesystem
location in submit_job task_prompt or extra_data. Never copy path values from
tool_data into the submitted job. When the job needs files found in the user's
folder, refer to them only with the exact phrase "requirements provided in
workspace". Only say "workspace"; do not identify or describe its filesystem
location. URLs and other non-file metadata are allowed in extra_data.

Follow reply_format exactly. Follow the selected action's action_args_format
exactly. Always include a concise step_summary describing the decision and the
information used.
            """,
            "action_avalailble" : ["send_message","submit_job","list_user_files"],
            "action_args_format" : {
                "send_message" : {
                    "message" : ""
                },
                "submit_job" : {
                    "task_prompt" : {"instructions": ""},
                    "task_title" : "",
                    "extra_data" : {}
                },
                "list_user_files" : {}
            },
            "reply_format": {
                "next_action" : "send_message|submit_job|list_user_files",
                "args" : {},
                "step_summary":""
            }
        }

        response = Agent.generate_llm(user_prompt, system_prompt)

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
            is_success = Agent.submit_job(title, task, job_extra_data)

            if is_success:
                Agent.update_context_memory(
                    "notify_user",
                    step_summary or "The complete task was submitted successfully.",
                )
                return Agent.decide_action(
                    "System: The job was submitted successfully. Inform the user without submitting it again.",
                    org_id,
                    user_id,
                    reset_context=False,
                    should_continue=should_continue,
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
                tool_data={"user_files": user_files},
                should_continue=should_continue,
            )

        return False

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
        
