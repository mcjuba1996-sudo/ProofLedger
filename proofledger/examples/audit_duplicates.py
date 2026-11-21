"""Аудитор дублей сверяет номера первичных счетов в таблице invoices.

Загрузите файл как Python-агента. Агент ищет только duplicate_invoice,
поэтому пропускает другие семейства ошибок в смешанных эпизодах.
Он не читает seed, эталон, файлы или сеть и не импортирует ProofLedger.
В новых эпизодах один номер первичного счёта у одного контрагента
обозначает одно обязательство. Для прежних эпизодов применяется
их правило по контрагенту, валюте, сумме и локальной дате.
"""


class Agent:
    def reset(self, observation, info):
        self.episode_id = info["episode_id"]
        self.timezone = observation["manifest"]["timezone"]
        self.cursor = None
        self.waiting = False
        self.done = False
        self.invoices = []

    def offset_minutes(self, offset):
        if offset == "Z":
            return 0
        minutes = int(offset[1:3]) * 60 + int(offset[4:6])
        return -minutes if offset[0] == "-" else minutes

    def month_days(self, year, month):
        if month == 2:
            leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
            return 29 if leap else 28
        return 30 if month in (4, 6, 9, 11) else 31

    def local_date(self, timestamp):
        year, month, day = int(timestamp[0:4]), int(timestamp[5:7]), int(timestamp[8:10])
        minutes = int(timestamp[11:13]) * 60 + int(timestamp[14:16])
        printed_offset = "Z" if timestamp.endswith("Z") else timestamp[-6:]
        minutes += self.offset_minutes(self.timezone) - self.offset_minutes(printed_offset)
        day += minutes // (24 * 60)
        while day < 1:
            month -= 1
            if month < 1:
                year, month = year - 1, 12
            day += self.month_days(year, month)
        while day > self.month_days(year, month):
            day -= self.month_days(year, month)
            month += 1
            if month > 12:
                year, month = year + 1, 1
        return year, month, day

    def submit(self, findings):
        return {
            "action": "submit",
            "findings": {"episode_id": self.episode_id, "findings": findings[:30]},
        }

    def act(self, observation):
        if self.waiting:
            self.waiting = False
            if observation.get("error"):
                # Не обвиняем компанию на основе неполученных документов.
                return self.submit([])
            result = observation.get("result") or {}
            self.invoices += result.get("records", [])
            self.cursor = result.get("next_cursor")
            self.done = self.cursor is None
        if not self.done:
            self.waiting = True
            return {"action": "list_records", "table": "invoices", "cursor": self.cursor, "limit": 50}

        groups = {}
        for invoice in self.invoices:
            if "source_invoice_number" in invoice:
                key = (invoice["entity_id"], invoice["source_invoice_number"])
            else:
                key = (invoice["entity_id"], invoice["currency"], invoice["total_minor"],
                       self.local_date(invoice["issued_at"]))
            if key not in groups:
                groups[key] = []
            groups[key].append(invoice)

        findings = []
        for key in sorted(groups):
            group = sorted(groups[key], key=lambda invoice: invoice["invoice_id"])
            first = group[0]
            for duplicate in group[1:]:
                findings.append({
                    "type": "duplicate_invoice",
                    "affected_ids": [first["invoice_id"], duplicate["invoice_id"]],
                    "claimed_amount_minor": duplicate["total_minor"],
                    "currency": duplicate["currency"],
                    "evidence_ids": [first["doc_id"], duplicate["doc_id"]],
                    "explanation": ("Один номер первичного счёта для одного контрагента указан в двух записях."
                                    if "source_invoice_number" in duplicate else
                                    "Совпадают контрагент, валюта, сумма и локальная дата выставления."),
                })
        return self.submit(findings)
