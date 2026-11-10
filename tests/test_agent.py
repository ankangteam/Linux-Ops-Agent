from contextlib import closing
import importlib
import json
import os
import shlex
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

with patch("dotenv.load_dotenv"):
    import config
    import agent
import shell_tools
import ssh_transport


class SSHTests(unittest.TestCase):
    def setUp(self):
        self.settings = patch.multiple(config, SSH_TARGET="root@host", SSH_PORT=22,
            SSH_AUTH="password", SSH_PASSWORD='sensitive', COMMAND_TIMEOUT=600, COMMAND_OUTPUT_LIMIT=5)
        self.settings.start()
        self.addCleanup(self.settings.stop)
        self.channel = Mock()
        self.channel.closed = False
        self.channel.recv_ready.side_effect = [True, False]
        self.channel.recv_stderr_ready.side_effect = [True, False]
        self.channel.recv.return_value = b"abcdef"
        self.channel.recv_stderr.return_value = b"failure"
        self.channel.exit_status_ready.return_value = True
        self.channel.recv_exit_status.return_value = 7
        self.channel.send.return_value = 2
        self.client = Mock()
        self.client.get_transport.return_value.open_session.return_value = self.channel
        self.factory = patch("paramiko.SSHClient", return_value=self.client)
        self.factory.start()
        self.addCleanup(self.factory.stop)

    def test_root_password_shell_and_stdin(self):
        command = "cat; printf 'hello' | cat > /tmp/a\nprintf done"
        with patch("builtins.input", side_effect=AssertionError("prompt")):
            result = ssh_transport.run(command, "ok")
        self.assertEqual(shlex.split(self.channel.exec_command.call_args.args[0]), ["bash", "-lc", command])
        self.assertEqual(bytes(self.channel.send.call_args.args[0]), b"ok")
        self.assertEqual(result, dict(exit_code=7, stdout="abcde", stderr="failu", timed_out=False, truncated=True))
        self.assertEqual(self.client.connect.call_args.kwargs["username"], "root")
        self.assertEqual(self.client.connect.call_args.kwargs["password"], "sensitive")
        self.client.load_host_keys.assert_not_called()
        self.client.load_system_host_keys.assert_not_called()
        self.client.save_host_keys.assert_not_called()
        self.channel.close.assert_called_once()
        self.client.close.assert_called_once()

    def test_key_and_no_limits(self):
        with patch.multiple(config, SSH_AUTH="key", COMMAND_TIMEOUT=0, COMMAND_OUTPUT_LIMIT=0), patch.object(Path, "is_file", return_value=True):
            result = ssh_transport.run("true")
        self.assertEqual(result["stdout"], "abcdef")
        self.assertFalse(result["truncated"])
        self.assertIn("key_filename", self.client.connect.call_args.kwargs)
        self.assertNotIn("password", self.client.connect.call_args.kwargs)
        self.client.get_transport.return_value.open_session.assert_called_once_with(timeout=None)

    def test_timeout_partial_output(self):
        self.channel.exit_status_ready.return_value = False
        with patch("ssh_transport.time.monotonic", side_effect=[0, 1, 601]):
            result = ssh_transport.run("sleep 900")
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["stdout"], "abcde")
        self.assertIsNone(result["exit_code"])
        self.client.close.assert_called_once()

    def test_auth_failure(self):
        import paramiko
        self.client.connect.side_effect = paramiko.AuthenticationException("bad")
        self.assertIn("authentication failed", ssh_transport.run("true")["error"])
        self.client.close.assert_called_once()

    def test_missing_password(self):
        with patch.object(config, "SSH_PASSWORD", ""), self.assertRaises(ValueError):
            ssh_transport.run("true")
        self.client.connect.assert_not_called()

    def test_accept_unknown_and_changed_keys(self):
        import paramiko
        ssh_transport.run("true")
        policy = self.client.set_missing_host_key_policy.call_args.args[0]
        self.assertIsInstance(policy, paramiko.AutoAddPolicy)
        # Every command creates an empty client, so no historical key comparison occurs.
        fresh = paramiko.client.SSHClient()
        self.assertEqual(len(fresh.get_host_keys()), 0)
        fresh.close()


class AgentTests(unittest.TestCase):
    def test_tool_contract(self):
        self.assertEqual([t["name"] for t in shell_tools.TOOLS], ["shell_command"])
        with patch.object(ssh_transport, "run", return_value={}) as run:
            shell_tools.execute("shell_command", {"command": "echo hi"})
            run.assert_called_once_with("echo hi", None)
        for args in [{}, {"command": 2}, {"command": "true", "stdin": 2}]:
            with self.assertRaises(ValueError):
                shell_tools.execute("shell_command", args)

    def test_config_defaults_and_migration(self):
        saved = dict(vars(config))
        try:
            with patch.dict(os.environ, {"USERPROFILE": str(Path.home())}, clear=True), patch("dotenv.load_dotenv"):
                importlib.reload(config)
                self.assertEqual((config.SSH_AUTH, config.MAX_STEPS), ("password", 128))
                for provider in ["deepseek", "openai", "qianwen"]:
                    with patch.dict(os.environ, {"OPS_PROVIDER": provider}):
                        importlib.reload(config)
                        self.assertEqual(config.PROVIDER_PREFIX, provider.upper())
                with patch.dict(os.environ, {"OPS_DOCKER_TIMEOUT": "42", "OPS_DOCKER_OUTPUT_LIMIT": "123"}):
                    importlib.reload(config)
                    self.assertEqual((config.COMMAND_TIMEOUT, config.COMMAND_OUTPUT_LIMIT), (42,123))
                    with patch.dict(os.environ, {"OPS_COMMAND_TIMEOUT":"0"}):
                        importlib.reload(config)
                        self.assertEqual(config.COMMAND_TIMEOUT, 0)
        finally:
            config.__dict__.update(saved)

    def test_audit_redacts_password(self):
        with tempfile.TemporaryDirectory(dir=".") as directory, patch.object(agent, "DB_PATH", Path(directory)/"audit.db"), patch.object(config,"SSH_PASSWORD", 'p"a\\ss'):
            agent.init_db()
            agent.audit("task", "test", {"value": config.SSH_PASSWORD})
            with closing(sqlite3.connect(agent.DB_PATH)) as con:
                details = json.loads(con.execute("select details from audit").fetchone()[0])
            self.assertEqual(details["value"], "[REDACTED]")

    def test_loop_limit_and_unlimited(self):
        call = SimpleNamespace(id="call", function=SimpleNamespace(name="shell_command", arguments='{"command":"true"}'))
        def response(calls):
            message = Mock(tool_calls=calls, content="done")
            message.model_dump.return_value = {"role":"assistant", "content":"done"}
            return SimpleNamespace(id="response", choices=[SimpleNamespace(message=message, finish_reason="stop")])
        for limit, expected in [(1, "none"), (0,"auto")]:
            client = Mock()
            client.chat.completions.create.side_effect = [response([call]), response([])]
            with patch.object(agent,"MAX_STEPS",limit), patch.object(agent,"audit"), patch.object(agent,"run_tool",return_value="{}"), patch("builtins.print"):
                agent.run_task(client,"test",[])
            self.assertEqual(client.chat.completions.create.call_args.kwargs["tool_choice"], expected)

    def test_interactive_commands(self):
        with patch.object(agent,"init_db"), patch.object(agent,"OpenAI"), patch.object(agent.ssh_transport,"validate_config"), patch.dict(os.environ, {agent.PROVIDER_PREFIX+"_API_KEY":"test"}), patch("sys.argv",["agent.py"]), patch("builtins.input", side_effect=["/new","/exit"]), patch("builtins.print") as output:
            agent.main()
        self.assertTrue(any("上下文已清空" in str(call) for call in output.call_args_list))


if __name__ == "__main__":
    unittest.main()
