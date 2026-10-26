"""Partes da análise da pergunta (src/query_analysis.py), separadas por assunto.

``entities``: órgão, município, fornecedor e CNPJ citados. A leitura de
valores e períodos, que é regra pura, fica no domínio
(src/domain/values_and_periods.py). ``src/query_analysis.py`` continua sendo a
entrada (``analisar_query``, ``filtros_para_retrieve``) e reexporta os nomes.
"""
