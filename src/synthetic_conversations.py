"""Validação e representação das conversas sintéticas geradas pelo tutor.

A classificação vem do documento, nunca do LLM nem do padrão da base.
Dados bancários e credenciais são restritos mesmo se rotulados como públicos.
"""

from __future__ import annotations

from typing import Any


RESTRICTED_GUARDRAILS = {"refuse_bank_info", "refuse_credentials"}
RESTRICTED_DATA_TYPES = {"bank_info", "bank_details", "bank_account", "credentials"}


def conversation_document(payload: Any) -> tuple[str, str, dict[str, Any]]:
    """Retorna texto, sensibilidade e metadados; rejeita registros incompletos."""
    if not isinstance(payload, dict) or not str(payload.get("conversation_id") or "").strip():
        raise ValueError("conversa sintética sem conversation_id")
    sensitivity = payload.get("sensitivity_level")
    if sensitivity not in {"publico", "restrito"}:
        raise ValueError("conversa sintética sem sensitivity_level válido")
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("conversa sintética sem mensagens")
    if any(not isinstance(message, dict) or not str(message.get("content") or "").strip()
           for message in messages):
        raise ValueError("mensagem sintética sem conteúdo")
    if payload.get("guardrail_type") in RESTRICTED_GUARDRAILS or any(
        RESTRICTED_DATA_TYPES.intersection(message.get("sensitive_data_types") or [])
        for message in messages
    ):
        sensitivity = "restrito"

    lines = [
        "[CONTEÚDO SINTÉTICO — cenário fictício para testes, não comprova fatos reais]",
        f"Conversa: {payload['conversation_id']}",
    ]
    for field, label in (
        ("title", "Título"), ("channel", "Canal"), ("organ", "Órgão"),
        ("subject", "Assunto"), ("creation_date", "Data da conversa"),
        ("process_number", "Processo"), ("arp_number", "ARP"),
        ("supplier_name", "Fornecedor"), ("supplier_cnpj", "CNPJ"),
    ):
        if payload.get(field):
            lines.append(f"{label}: {payload[field]}")
    for message in messages:
        lines.append("")
        for field, label in (
            ("timestamp", "Data"), ("sender_name", "De"), ("sender_role", "Função"),
            ("sender_email", "E-mail"), ("recipient", "Para"),
        ):
            if message.get(field):
                lines.append(f"{label}: {message[field]}")
        lines.append(str(message["content"]).strip())

    extras = {
        key: payload[key] for key in (
            "conversation_id", "title", "channel", "organ", "subject", "creation_date",
            "process_number", "arp_number", "supplier_name", "supplier_cnpj",
            "sensitivity_level", "guardrail_type", "tags",
        ) if payload.get(key) is not None
    }
    extras.update(
        sintetico=True,
        titulo_documento=payload.get("title") or payload["conversation_id"],
        orgao=payload.get("organ"),
        cnpj=payload.get("supplier_cnpj"),
        numero_processo=payload.get("process_number"),
        total_mensagens=len(messages),
    )
    return "\n".join(lines), sensitivity, extras
