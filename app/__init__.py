"""Agente de consultas y gestión del gimnasio (punto 3 de la consigna).

Paquetes:
    - ``app.db``: conexiones por rol y lectura validada de la base (text-to-SQL).
    - ``app.rag``: ingesta de los PDFs en Chroma y búsqueda de fragmentos.
    - ``app.ops``: catálogo de operaciones de escritura sobre socios.
    - ``app.agent``: grafo LangGraph que rutea, consulta y redacta.
    - ``app.ui``: interfaz Streamlit.
"""
