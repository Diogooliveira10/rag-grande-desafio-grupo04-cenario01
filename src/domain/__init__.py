"""Regras de negócio do NEXO RJ, sem infraestrutura (camada de domínio).

Aqui ficam as decisões que não dependem de onde os dados estão nem de qual
modelo responde: a política de confiança, a situação da compra que entra nas
contas, a fusão dos rankings da busca. São funções puras, testáveis sem banco,
índice, LLM nem rede.

Regra da camada (conferida por tests/test_architecture.py): os módulos daqui só
importam a biblioteca padrão, ``src.schema`` (as entidades) e outros módulos de
``src.domain``. Nada de ``config``, SQLite, FAISS, cliente HTTP ou FastAPI: quem
precisa disso são os adaptadores, que chamam o domínio, e não o contrário.
"""
