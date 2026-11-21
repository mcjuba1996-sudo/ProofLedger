"""Optional gymnasium.Env wrapper (only if gymnasium is installed; not needed for evaluation).

    from proofledger.gym_adapter import ProofLedgerGymEnv
    env = ProofLedgerGymEnv(mode="train", level="L3")
    obs, info = env.reset(seed=1)            # obs is a JSON string
    obs, reward, terminated, truncated, info = env.step('{"action": "list_records", "table": "invoices"}')

Observations and actions are JSON strings in gymnasium.spaces.Text, so the env passes
gymnasium.utils.env_checker.check_env. The JSON contract itself is unchanged.
"""
import json
import string

import gymnasium as gym
from gymnasium import spaces

from .env import Environment

_CHARS = string.printable


class ProofLedgerGymEnv(gym.Env):
    metadata = {"render_modes": ["ansi"], "render_fps": 4}

    def __init__(self, mode="train", level=None, config=None, render_mode=None):
        self._env = Environment(mode, render_mode=render_mode)
        self.render_mode = render_mode
        self._config = dict(config or {}, **({"level": level} if level else {}))
        self.observation_space = spaces.Text(min_length=0, max_length=2_000_000, charset=_CHARS)
        self.action_space = spaces.Text(min_length=2, max_length=100_000, charset=_CHARS)

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        if seed is None:
            seed = int(self.np_random.integers(0, 2 ** 62))
        obs, info = self._env.reset(seed, options if options is not None else (self._config or None))
        return json.dumps(obs), info

    def step(self, action):
        try:
            a = json.loads(action) if isinstance(action, str) else action
        except ValueError:
            a = "invalid"
        obs, r, te, tr, info = self._env.step(a)
        return json.dumps(obs), float(r), te, tr, info

    def render(self):
        return self._env.render()

    def close(self):
        self._env.close()
