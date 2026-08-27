"""Persistent local-network TCP connection to the Swarif agent."""

import json
import os
import select
import socket
import threading
import time

from PyQt5.QtCore import QObject, pyqtSignal


DEFAULT_SERVER_PORT = int(os.getenv("SWARIF_AGENT_PORT", "8767"))
DEFAULT_CONNECTION_CHECK_INTERVAL = 2


class ServerConnection(QObject):
    """Maintain one background connection and heartbeat loop per application."""

    status_changed = pyqtSignal(bool)
    completion_received = pyqtSignal(str, object)
    progress_received = pyqtSignal(str, object)
    cancellation_succeeded = pyqtSignal(str)
    cancellation_failed = pyqtSignal(str, str)
    cancellation_connection_lost = pyqtSignal(str)
    log_message = pyqtSignal(str)

    def __init__(
        self,
        config_provider,
        server_port=DEFAULT_SERVER_PORT,
        timeout=DEFAULT_CONNECTION_CHECK_INTERVAL,
        retry_interval=DEFAULT_CONNECTION_CHECK_INTERVAL,
        heartbeat_interval=DEFAULT_CONNECTION_CHECK_INTERVAL,
        parent=None,
    ):
        super().__init__(parent)
        self.config_provider = config_provider
        self.default_server_port = server_port
        self.timeout = timeout
        self.retry_interval = retry_interval
        self.heartbeat_interval = heartbeat_interval
        self._connected = False
        self._thread = None
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._socket = None
        self._receive_buffer = bytearray()
        self._last_cancel_error = None
        self._state_lock = threading.Lock()
        self._socket_lock = threading.Lock()
        # A listener and a request must never consume each other's response.
        self._packet_lock = threading.Lock()

    @property
    def connected(self):
        with self._state_lock:
            return self._connected

    def start(self):
        """Start the worker once. Repeated calls are harmless."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._wake_event.clear()
        self._thread = threading.Thread(
            target=self.connection_loop,
            name="swarif-server-connection",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        """Stop retries/heartbeats and close the socket to unblock I/O."""
        self._stop_event.set()
        self._wake_event.set()
        self._close_socket()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=self.timeout + 1)
        self._thread = None
        self._set_connected(False)

    def reconnect(self):
        """Wake the existing worker after login or a configuration change."""
        self._set_connected(False)
        self._close_socket()
        self._wake_event.set()

    def connection_loop(self):
        self._log(f"TCP connection monitor started ({self.retry_interval}-second interval)")
        try:
            while not self._stop_event.is_set():
                config = self.config_provider() or {}
                user_id = config.get("user_id") or config.get("id")
                server_ip = config.get("server_ip") or config.get("agent_ip")
                server_port = config.get("server_port", self.default_server_port)

                if not user_id or not server_ip:
                    self._set_connected(False)
                    missing = []
                    if not user_id:
                        missing.append("user ID")
                    if not server_ip:
                        missing.append("agent IP")
                    self._log(
                        f"Connection attempt skipped: missing {' and '.join(missing)}; checking again in {self.retry_interval} seconds"
                    )
                    self._wait(self.retry_interval)
                    continue

                attempt_started = time.monotonic()
                try:
                    server_port = int(server_port)
                    if not 1 <= server_port <= 65535:
                        raise ValueError
                    if not self.connected:
                        self._log(f"Connecting to {server_ip}:{server_port}")
                        if not self.connect_to_server(server_ip, server_port, user_id):
                            self._log("Connection handshake was rejected")
                            self._set_connected(False)
                            self._log(f"Next connection attempt in {self.retry_interval} seconds")
                            self._wait_for_retry(attempt_started)
                            continue
                        self._set_connected(True)
                        self._log(f"Connected to {server_ip}:{server_port}")

                    if not self.listen_for_server(user_id):
                        self._set_connected(False)
                        self._close_socket()
                except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
                    self._log(f"Connection error: {error}")
                    self._set_connected(False)
                    self._close_socket()
                    self._log(f"Next connection attempt in {self.retry_interval} seconds")
                    self._wait_for_retry(attempt_started)
        finally:
            self._close_socket()
            self._set_connected(False)

    def connect_to_server(self, server_ip, server_port, user_id):
        """Open the TCP socket and perform the initial connect handshake."""
        self._log(f"Opening TCP socket to {server_ip}:{server_port}")
        sock = socket.create_connection(
            (str(server_ip), server_port),
            timeout=self.timeout,
        )
        sock.settimeout(self.timeout)
        with self._socket_lock:
            self._socket = sock
            self._receive_buffer.clear()
        client_ip = sock.getsockname()[0]
        response = self.send_packet(
            {
                "type": "connect",
                "user_id": str(user_id),
                "ip": client_ip,
            }
        )
        if response.get("status") != "ok":
            self._close_socket()
            return False
        return True

    def send_heartbeat(self, user_id):
        response = self.send_packet({"type": "hi", "user_id": str(user_id)})
        return response.get("status") == "ok"

    def listen_for_server(self, user_id):
        """Receive server events continuously and send scheduled heartbeats."""
        next_heartbeat = time.monotonic() + self.heartbeat_interval
        while not self._stop_event.is_set() and self.connected:
            if self._wake_event.is_set():
                self._wake_event.clear()
                return False

            with self._socket_lock:
                sock = self._socket
            if sock is None:
                return False

            remaining = max(0, next_heartbeat - time.monotonic())
            readable, _, exceptional = select.select(
                [sock], [], [sock], min(0.25, remaining)
            )
            if exceptional:
                return False
            if readable:
                with self._packet_lock:
                    response = self.receive_packet(sock)
                if self._handle_server_packet(response):
                    continue
                if response.get("status") == "bad":
                    return False

            if time.monotonic() >= next_heartbeat:
                if not self.send_heartbeat(user_id):
                    return False
                next_heartbeat = time.monotonic() + self.heartbeat_interval
        return False

    def send_packet(self, packet):
        """Send one UTF-8 JSON packet and read one JSON response."""
        with self._packet_lock:
            with self._socket_lock:
                sock = self._socket
            if sock is None:
                raise ConnectionError("The local server socket is not connected")

            self._log(f"SEND {json.dumps(packet, ensure_ascii=False)}")
            sock.sendall(json.dumps(packet).encode("utf-8"))
            while True:
                response = self.receive_packet(sock)
                if self._handle_server_packet(response):
                    continue
                return response

    def cancel_job(self, user_id, job_id):
        """Cancel exactly one job over the existing connection."""
        user_id = str(user_id or "").strip()
        job_id = str(job_id or "").strip()
        if not user_id or not job_id:
            error = "Cancellation requires a user_id and job_id"
            self._last_cancel_error = error
            self.cancellation_failed.emit(job_id, error)
            return False
        if not self.connected:
            error = "The local server connection is unavailable; cancellation was not confirmed"
            self._last_cancel_error = error
            self.cancellation_connection_lost.emit(job_id)
            self.cancellation_failed.emit(job_id, error)
            return False
        try:
            self._log(
                f"cancel_job SEND user_id={user_id} job_id={job_id}"
            )
            response = self.send_packet(
                {"type": "cancel_job", "user_id": user_id, "job_id": job_id}
            )
            self._log(
                f"cancel_job RESPONSE user_id={user_id} job_id={job_id} "
                f"response={json.dumps(response, ensure_ascii=False, default=str)}"
            )
            self._last_cancel_error = None
        except (OSError, socket.error, ConnectionError, ValueError) as error:
            self._last_cancel_error = str(error)
            self._log(
                f"cancel_job RESPONSE user_id={user_id} job_id={job_id} "
                f"error={error}"
            )
            self._set_connected(False)
            self.cancellation_connection_lost.emit(job_id)
            self.cancellation_failed.emit(job_id, str(error))
            return False
        accepted = (
            isinstance(response, dict)
            and response.get("status") == "ok"
            and response.get("type") == "cancel_job"
            and response.get("job_id") == job_id
            and response.get("cancelled") is True
        )
        if accepted:
            self.cancellation_succeeded.emit(job_id)
            return True
        error = "The server did not confirm cancellation for this job"
        self._last_cancel_error = error
        self.cancellation_failed.emit(job_id, error)
        return False

    def receive_packet(self, sock=None):
        """Read and decode one UTF-8 JSON packet from the active socket."""
        if sock is None:
            with self._socket_lock:
                sock = self._socket
        if sock is None:
            raise ConnectionError("The local server socket is not connected")

        decoder = json.JSONDecoder()
        while len(self._receive_buffer) < 65536:
            try:
                text = self._receive_buffer.decode("utf-8")
            except UnicodeDecodeError:
                text = ""
            if text:
                stripped = text.lstrip()
                try:
                    decoded, end = decoder.raw_decode(stripped)
                except json.JSONDecodeError:
                    pass
                else:
                    self._receive_buffer = bytearray(
                        stripped[end:].lstrip().encode("utf-8")
                    )
                    if not isinstance(decoded, dict):
                        raise ValueError("The local server response must be a JSON object")
                    self._log(f"RECV {json.dumps(decoded, ensure_ascii=False)}")
                    return decoded

            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("The local server closed the connection")
            self._receive_buffer.extend(chunk)
        raise ValueError("The local server response is too large")

    def _handle_server_packet(self, packet):
        """Dispatch asynchronous packets; return True when handled."""
        packet_type = packet.get("type")
        if packet_type == "progress":
            job_id = packet.get("job_id")
            if isinstance(job_id, str) and job_id.strip():
                self.progress_received.emit(
                    job_id.strip(), packet.get("current_progress")
                )
            return True
        if packet_type != "completion":
            return False
        job_id = packet.get("job_id")
        if isinstance(job_id, str) and job_id.strip():
            self.completion_received.emit(
                job_id.strip(), packet.get("completion_feedback")
            )
        return True

    def _wait(self, seconds):
        """Wait interruptibly; return True when explicitly woken."""
        self._wake_event.wait(seconds)
        was_woken = self._wake_event.is_set()
        self._wake_event.clear()
        return was_woken

    def _wait_for_retry(self, attempt_started=None):
        """Keep disconnected connection attempts on the configured cadence."""
        elapsed = 0 if attempt_started is None else time.monotonic() - attempt_started
        self._wait(max(0, self.retry_interval - elapsed))

    def _set_connected(self, connected):
        connected = bool(connected)
        with self._state_lock:
            changed = connected != self._connected
            self._connected = connected
        if changed:
            self.status_changed.emit(connected)
            if not connected:
                self._log("Disconnected")

    def _log(self, message):
        self.log_message.emit(str(message))

    def _close_socket(self):
        with self._socket_lock:
            sock = self._socket
            self._socket = None
            self._receive_buffer.clear()
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
