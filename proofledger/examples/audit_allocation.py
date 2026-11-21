"""Аудитор распределения: документы платежей и проводки счёта 2900.

Загрузите файл как Python-агента. Агент ищет только payment_allocation.
Он читает таблицы через разрешённые действия, использует сумму проводки
в базовой валюте и подтверждает вывод документом платежа и проводкой.
На других семействах ошибок неполная полнота является ожидаемой.
Импорты, чтение файлов, сеть и доступ к эталону не используются.
"""


class Agent:
    def reset(self, observation, info):
        self.episode_id = info["episode_id"]
        self.base_currency = observation["manifest"]["base_currency"]
        self.tables = ["payments", "ledger"]
        self.records = {"payments": [], "ledger": []}
        self.table_index = 0
        self.cursor = None
        self.waiting = False

    def submit(self, findings):
        return {
            "action": "submit",
            "findings": {"episode_id": self.episode_id, "findings": findings[:30]},
        }

    def act(self, observation):
        if self.waiting:
            self.waiting = False
            if observation.get("error"):
                return self.submit([])
            result = observation.get("result") or {}
            table = self.tables[self.table_index]
            self.records[table] += result.get("records", [])
            self.cursor = result.get("next_cursor")
            if self.cursor is None:
                self.table_index += 1
        if self.table_index < len(self.tables):
            self.waiting = True
            return {"action": "list_records", "table": self.tables[self.table_index],
                    "cursor": self.cursor, "limit": 50}

        payment_documents = {}
        for payment in self.records["payments"]:
            payment_documents[payment["payment_id"]] = payment["doc_id"]
        findings = []
        for row in self.records["ledger"]:
            payment_id = row.get("payment_id")
            if (row["account"] == "2900" and row["currency"] == self.base_currency
                    and row["credit_minor"] > 0 and payment_id in payment_documents):
                findings.append({
                    "type": "payment_allocation",
                    "affected_ids": [payment_id],
                    "claimed_amount_minor": row["credit_minor"],
                    "currency": self.base_currency,
                    "evidence_ids": [payment_documents[payment_id], row["row_id"]],
                    "explanation": "Поступление отражено по кредиту счёта 2900 "
                                   "вместо распределения на дебиторскую задолженность.",
                })
        return self.submit(findings)
