"""Контрольный агент: сразу отправляет пустой ответ.

Загрузите этот файл как Python-агента. На чистом эпизоде ожидается F1=1,
на эпизоде с ошибками F1=0. Нужен для сравнения с настоящими аудиторами.
Использует только публичный episode_id, полученный при reset.
"""


class Agent:
    def reset(self, observation, info):
        self.episode_id = info["episode_id"]

    def act(self, observation):
        return {
            "action": "submit",
            "findings": {"episode_id": self.episode_id, "findings": []},
        }
