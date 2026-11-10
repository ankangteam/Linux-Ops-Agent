"""Paramiko transport shared by every remote operation."""
import shlex
import socket
import time
from pathlib import Path
import config


def validate_config():
    if "@" not in config.SSH_TARGET:
        raise ValueError("请设置 OPS_SSH_TARGET=user@host")
    user, host = config.SSH_TARGET.split("@", 1)
    if not user or not host:
        raise ValueError("请设置 OPS_SSH_TARGET=user@host")
    if not 1 <= config.SSH_PORT <= 65535:
        raise ValueError("OPS_SSH_PORT must be between 1 and 65535")
    if config.SSH_AUTH not in {"password", "key"}:
        raise ValueError("OPS_SSH_AUTH must be password or key")
    if config.SSH_AUTH == "password" and not config.SSH_PASSWORD:
        raise ValueError("请设置 OPS_SSH_PASSWORD")
    if config.SSH_AUTH == "key" and not Path(config.SSH_IDENTITY).is_file():
        raise ValueError("SSH identity not found: " + config.SSH_IDENTITY)


def run(command, stdin=None):
    import codecs
    import paramiko
    validate_config()
    user, host = config.SSH_TARGET.split("@", 1)
    client = paramiko.SSHClient()
    # Deliberately use an empty in-memory host-key store, never read/write known_hosts.
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    result = {"exit_code": None, "stdout": "", "stderr": "", "timed_out": False, "truncated": False}
    limit = config.COMMAND_OUTPUT_LIMIT
    decoders = {key: codecs.getincrementaldecoder("utf-8")("replace") for key in ("stdout", "stderr")}

    def append(key, chunk, final=False):
        text = decoders[key].decode(chunk, final=final)
        remaining = max(0, limit - len(result[key])) if limit else len(text)
        if len(text) > remaining:
            result["truncated"] = True
        result[key] += text[:remaining]

    channel = None
    try:
        auth = {"password": config.SSH_PASSWORD} if config.SSH_AUTH == "password" else {"key_filename": config.SSH_IDENTITY}
        client.connect(hostname=host.strip("[]"), port=config.SSH_PORT, username=user,
                       look_for_keys=False, allow_agent=False, timeout=8, banner_timeout=8, auth_timeout=8, **auth)
        timeout = config.COMMAND_TIMEOUT or None
        deadline = time.monotonic() + timeout if timeout else None
        channel = client.get_transport().open_session(timeout=timeout)
        channel.settimeout(timeout)
        channel.exec_command("bash -lc " + shlex.quote(command))
        channel.settimeout(0.0)
        pending = memoryview((stdin or "").encode("utf-8"))
        sent_eof = False
        while True:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("command deadline exceeded")
            progressed = False
            # Drain both streams even after reaching the display limit to avoid SSH deadlocks.
            for key, ready, receive in [("stdout", channel.recv_ready, channel.recv),
                                         ("stderr", channel.recv_stderr_ready, channel.recv_stderr)]:
                if ready():
                    append(key, receive(32768))
                    progressed = True
            if pending and not channel.closed and channel.send_ready():
                count = channel.send(pending[:32768])
                if count:
                    pending = pending[count:]
                    progressed = True
                else:
                    pending = memoryview(b"")
            if not pending and not sent_eof:
                channel.shutdown_write()
                sent_eof = True
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                result["exit_code"] = channel.recv_exit_status()
                break
            if channel.closed:
                raise RuntimeError("SSH channel closed before receiving an exit status")
            if not progressed:
                time.sleep(0.01)
    except (TimeoutError, socket.timeout):
        result["timed_out"] = True
        result["error"] = "SSH timed out; remote execution may still be running. Check state before retrying."
    except paramiko.AuthenticationException:
        result["error"] = "SSH authentication failed; check account, authentication mode and credentials."
    except Exception as exc:
        result["error"] = f"SSH execution failed: {exc}"
    finally:
        if channel is not None:
            channel.close()
        client.close()
        for key in decoders:
            append(key, b"", final=True)
    # Prevent a remote echo or exception from disclosing the configured login password.
    if config.SSH_PASSWORD:
        for key, value in result.items():
            if isinstance(value, str):
                result[key] = value.replace(config.SSH_PASSWORD, "[REDACTED]")
    return result
