"""Official Genesis FinTech adapter, imported in the trusted judging process.

The judge owns this object, paths, seeds, and scores. Its separate, restricted
policy process receives only the JSON observations, rewards, flags and public
info, and sends back actions. See README.md for the wire contract.
"""
import logging

from proofledger import matcher
from proofledger.env import MAX_STEPS, Environment
from proofledger.generator import generate_episode as build_episode
from proofledger.generator import read_episode, write_episode
from proofledger.levels import expand


_LOG = logging.getLogger(__name__)
_ERROR_MESSAGES = {
    "division_by_zero": "The expression divides by zero.",
    "episode_done": "The episode has ended.",
    "invalid_action": "Expected an action name and an arguments object.",
    "invalid_cursor": "Invalid page cursor.",
    "invalid_expression": "Invalid arithmetic expression.",
    "invalid_limit": "The page limit must be a positive integer.",
    "invalid_submission": "Invalid submission structure or episode identifier.",
    "overflow": "The calculation exceeds the permitted range.",
    "truncated": "The episode budget has been exhausted.",
    "unknown_action": "Unknown action name.",
    "unknown_id": "No public record was found for this identifier.",
    "unknown_table": "Unknown table name.",
}


def generate_episode(seed: int, config: dict, output_dir: str) -> None:
    """Write the six public files and the judge's truth and provenance files."""
    episode = build_episode(seed, expand(config, seed))
    write_episode(episode, output_dir, include_private=True)


def score_episode(episode_dir: str, findings: dict, inspection_steps: int) -> dict:
    """Independently score the supplied submission against this exact directory."""
    if type(inspection_steps) is not int or not 0 <= inspection_steps <= MAX_STEPS:
        raise ValueError(f"inspection_steps must be an integer from 0 to {MAX_STEPS}")
    episode = read_episode(episode_dir)
    metrics, error = matcher.score_submission(episode, findings, inspection_steps)
    if error is not None:
        _LOG.warning("Invalid submission for episode %s: %s", episode["manifest"]["episode_id"], error)
    return metrics


class _JudgeEnvironment:
    def __init__(self):
        self._core = Environment(mode="closed")

    def reset(self, seed, config):
        """Load episode_dir verbatim, or generate from the seed and team_config."""
        if not isinstance(config, dict):
            raise ValueError("reset config must be an object")
        mode = config.get("mode", "closed")
        if mode not in ("closed", "train"):
            raise ValueError("mode must be 'closed' or 'train'")
        if config.get("episode_dir") is not None:
            options = {"episode_path": config["episode_dir"]}
        else:
            options = config.get("team_config", {})
            if not isinstance(options, dict) or "episode_path" in options:
                raise ValueError("team_config must contain generator settings")
        obs, info = self._core.reset(seed, options)
        self._core.mode = mode
        return {
            "task": obs["task"], "manifest": obs["manifest"], "policy": obs["policy"],
            "remaining_steps": obs["steps_remaining"],
        }, {"episode_id": info["episode_id"], "version": info["version"]}

    def step(self, action):
        name = action.get("name") if isinstance(action, dict) else None
        args = action.get("args") if isinstance(action, dict) else None
        if isinstance(name, str) and isinstance(args, dict):
            native = {**args, "action": name}
        elif name == "submit":
            native = {"action": "submit"}  # An invalid submission still terminates.
        else:
            native = None  # Environment consumes the erroneous action and its step.
        obs, reward, terminated, truncated, info = self._core.step(native)
        result = obs["result"]
        error = obs["error"]
        if error is not None:
            code = error["code"]
            error = {"code": code, "message": _ERROR_MESSAGES.get(code, "The action failed.")}
            if code == "invalid_submission":
                _LOG.warning("Invalid submission for episode %s: %s",
                             info["episode_id"], self._core.final_report()["error"])
        elif name == "get_record":
            result = result["record"]
        elif name == "get_document":
            result = result["document"]
        elif name == "calculator":
            result = result["value"]
        elif name == "submit":
            result = {"accepted": True}
        public_info = {key: info[key] for key in ("episode_id", "step") if key in info}
        return ({"result": result, "error": error, "remaining_steps": obs["steps_remaining"]},
                reward, terminated, truncated, public_info)


def make_env():
    """Create an environment for the judge; reset selects train or closed mode."""
    return _JudgeEnvironment()
