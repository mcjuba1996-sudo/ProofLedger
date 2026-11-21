"""JSON-lines adapter executed *inside* WASI, in the same guest as the policy."""
import json
import sys


def main():
    incoming, outgoing = sys.stdin, sys.stdout
    # Ordinary print() diagnostics from a policy must not corrupt the response stream.
    sys.stdout = sys.stderr
    agent = None
    for line in incoming:
        try:
            request = json.loads(line)
            operation = request["op"]
            if operation == "ready" and agent is None:
                response = {"ok": True, "ready": True}
            elif operation == "load" and agent is None:
                namespace = {"__name__": "judge_agent", "__file__": "agent.py"}
                exec(compile(request["source"], "agent.py", "exec"), namespace)
                agent = namespace["Agent"]()
                response = {"ok": True}
            elif operation == "reset" and agent is not None:
                agent.reset(request["observation"], request["info"])
                response = {"ok": True}
            elif operation == "act" and agent is not None:
                action = agent.act(request["observation"])
                if not isinstance(action, dict):
                    raise TypeError("act() должен вернуть словарь действия.")
                response = {"ok": True, "action": action}
            elif operation == "close":
                return
            else:
                raise ValueError("Неизвестная операция.")
            payload = json.dumps(response, ensure_ascii=True, allow_nan=False)
        except BaseException as error:
            payload = json.dumps({"ok": False, "error": type(error).__name__ + ": " + str(error)[:220]})
        outgoing.write(payload + "\n")
        outgoing.flush()


main()
