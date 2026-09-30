-- Compilação offline, sem execução. Parâmetros: ["v6", 2025, 2025, 10]
SELECT fornecedor, SUM(valor) AS resultado FROM contratos_fatos WHERE inventory_version = %s AND EXTRACT(YEAR FROM data_assinatura) BETWEEN %s AND %s GROUP BY fornecedor ORDER BY resultado DESC, fornecedor LIMIT %s;
