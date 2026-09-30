-- Compilação offline, sem execução. Parâmetros: ["v6", 2025, 2025, "%manutenc%", "%computador%", 10]
SELECT modalidade, COUNT(*) AS resultado FROM contratos_fatos WHERE inventory_version = %s AND EXTRACT(YEAR FROM data_assinatura) BETWEEN %s AND %s AND objeto_normalizado LIKE %s AND objeto_normalizado LIKE %s GROUP BY modalidade ORDER BY resultado DESC, modalidade LIMIT %s;
