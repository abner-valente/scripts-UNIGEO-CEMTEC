"""Relatório Excel com uma aba por variável."""
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill

COR_CABECALHO = "1F4E78"
LARGURA_COLUNA = 25


def salvar_relatorio(tabelas: dict[str, pd.DataFrame], caminho: Path) -> None:
    """Grava cada tabela em uma aba do .xlsx, com cabeçalho formatado."""
    if not tabelas:
        print("⚠️ Nenhum dado foi coletado. O arquivo Excel não será gerado.")
        return

    caminho.parent.mkdir(parents=True, exist_ok=True)
    preenchimento = PatternFill(start_color=COR_CABECALHO, end_color=COR_CABECALHO, fill_type="solid")
    fonte = Font(color="FFFFFF", bold=True)
    alinhamento = Alignment(horizontal="center")

    with pd.ExcelWriter(caminho, engine="openpyxl") as writer:
        for aba, tabela in tabelas.items():
            tabela.to_excel(writer, sheet_name=aba, index=False)
            planilha = writer.sheets[aba]
            for celula in planilha[1]:
                celula.fill, celula.font, celula.alignment = preenchimento, fonte, alinhamento
            for coluna in planilha.columns:
                planilha.column_dimensions[coluna[0].column_letter].width = LARGURA_COLUNA

    print(f"✅ Relatório Excel salvo em: {caminho}")
