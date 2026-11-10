"""Single public tool for remote Shell execution."""
import ssh_transport

TOOLS = [{
    "type": "function", "name": "shell_command",
    "description": "Execute any command on the remote Linux host using bash -lc. Supports pipelines, redirection and multiline scripts. Optional stdin supplies UTF-8 input. No terminal or approval prompt. Check exit_code, stdout, stderr, timed_out and truncated; timeout does not mean remote execution stopped.",
    "parameters": {"type": "object", "properties": {
        "command": {"type": "string"}, "stdin": {"type": "string"}
    }, "required": ["command"], "additionalProperties": False},
}]


def execute(name, args):
    if name != "shell_command" or not isinstance(args, dict):
        raise ValueError("Unknown tool or invalid arguments")
    if set(args) - {"command", "stdin"} or not isinstance(args.get("command"), str):
        raise ValueError("command must be a string; only command and stdin are supported")
    if "stdin" in args and not isinstance(args["stdin"], str):
        raise ValueError("stdin must be a string")
    return ssh_transport.run(args["command"], args.get("stdin"))
