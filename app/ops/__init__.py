"""Catálogo de operaciones de escritura sobre socios (OP-01 a OP-05, spec técnico §7).

- ``modelos``: salida de ``extraer_operacion`` y propuesta de cambio.
- ``validacion``: reglas de negocio en código (ninguna depende del LLM).
- ``ejecucion``: transacción atómica con el rol escritor, revalidación y auditoría.
- ``auditoria``: registro de operaciones ejecutadas, canceladas, descartadas, rechazadas y fallidas.
- ``mensajes``: plantillas en castellano e inglés.
"""
