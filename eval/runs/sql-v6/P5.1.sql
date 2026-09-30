-- Compilação offline, sem execução. Parâmetros: ["v6", 2025, 2025, 10]
SELECT numero_controle_pncp, MAX(valor) AS resultado FROM contratos_fatos WHERE inventory_version = %s AND EXTRACT(YEAR FROM data_assinatura) BETWEEN %s AND %s GROUP BY numero_controle_pncp ORDER BY resultado DESC, numero_controle_pncp LIMIT %s;
