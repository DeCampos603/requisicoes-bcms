# -*- coding: utf-8 -*-
import io
from pathlib import Path
from docx import Document
from app import anexos, config, docx_docs, textos


def test_gerar_docx_requisicao(tmp_path):
    doc_dict = {
        "tipo": "REQUISICAO",
        "identificacao": {
            "titulo": "DIEx nº 100-SecSau/BCMS",
            "nup": "64000.000001/2026-01",
            "data": "7 de outubro de 2026.",
            "do": "Chefe da Seção de Saúde",
            "ao": "Ordenador de Despesas do BCMS",
            "assunto": "Aquisição de medicamentos.",
        },
        "anexos": ["Cópia da Nota de Crédito nº 2026NC432346."],
        "paragrafos": [
            "1. Solicito autorização para aquisição de medicamentos conforme <b>Pregão nº 90001/2026</b>.",
            "1.1 A requisição importa a responsabilidade do requisitante.",
        ],
        "quadro": [
            ("Tipo de Empenho:", "ORDINÁRIO"),
            ("Contrato:", "Sem instrumento de contrato (art. 95)"),
            ("PCA / Classe:", "Classe 6505"),
        ],
        "tabelas": [
            {
                "cabecalho": "00.000.000/0001-91 - FORNECEDOR TESTE LTDA",
                "linhas": [
                    {
                        "item": "01",
                        "si": "01",
                        "descricao": "MEDICAMENTO TESTE 500MG",
                        "und": "CX",
                        "qtde": "100",
                        "valor_unit": "R$ 10,00",
                        "total": "R$ 1.000,00",
                    }
                ],
                "total": "R$ 1.000,00",
            }
        ],
        "total_geral": "R$ 1.000,00",
        "assinatura": {
            "nome": "FULANO DE TAL SILVA – Cap Med",
            "funcao": "Chefe da Seção de Saúde",
        },
        "despacho": {
            "fiscal": {
                "titulo": "APROVAÇÃO DO FISCAL ADMINISTRATIVO",
                "texto": ["Aprovo a despesa."],
                "nome": "BELTRANO – Maj",
                "funcao": "Fiscal Administrativo",
            },
            "od": {
                "titulo": "DESPACHO DO ORDENADOR DE DESPESAS",
                "texto": ["1. Autorizo a despesa."],
                "nome": "CICLANO – Ten Cel",
                "funcao": "Ordenador de Despesas",
            },
        },
    }

    destino = tmp_path / "requisicao_teste.docx"
    resultado = docx_docs.gerar_docx_documento(doc_dict, destino)

    assert resultado.is_file()
    assert resultado.stat().st_size > 1000

    doc = Document(str(resultado))
    textos_doc = [p.text for p in doc.paragraphs]
    texto_total = " ".join(textos_doc)

    assert "DIEx nº 100-SecSau/BCMS" in texto_total or any("100-SecSau" in c.text for tbl in doc.tables for row in tbl.rows for c in row.cells)
    assert "Medicamento" in texto_total or "medicamentos" in texto_total
    assert len(doc.tables) >= 2  # Pelo menos tabela de identificação, itens e despacho


def test_localizar_anexo_nc_nova():
    # As 3 novas NCs adicionadas hoje pelo usuário
    for chave in ("2026NC432346", "2026NC432361", "2026NC432376"):
        fonte = {"nc": chave, "ug": "160329"}
        arq = anexos.localizar_anexo_nc(fonte)
        assert arq is not None, f"Anexo da NC {chave} deveria ser localizado em dados/nc ou EMPENHOS"
        assert arq.is_file(), f"Arquivo {arq} deve existir"


def test_paginacao_e_rodape_pdf(tmp_path, repo):
    """Testa a padronização das 3 páginas do DIEx: P1=Texto, P2=Tabela+Assinatura, P3=Despacho, com rodapés em todas."""
    import pymupdf
    from app import pdf_docs, regras, textos
    from conftest import payload_requisicao

    p = payload_requisicao()
    av = regras.avaliar_requisicao(p)
    doc_dict = textos.montar_requisicao(av.norm)

    caminho_pdf = tmp_path / "diex_paginado.pdf"
    pdf_docs.gerar_pdf_documento(doc_dict, caminho_pdf)

    assert caminho_pdf.is_file()
    doc = pymupdf.open(str(caminho_pdf))
    assert len(doc) >= 3, f"O DIEx padronizado deve ter no mínimo 3 páginas, obteve {len(doc)}"

    txt_p1 = doc[0].get_text()
    txt_p2 = doc[1].get_text()
    txt_p3 = doc[2].get_text()

    # Página 1: Identificação e Justificativa (sem tabela nem despacho)
    assert "DIEx Requisitório" in txt_p1
    assert "NUP: 64619" in txt_p1
    assert "DESPACHO DO ORDENADOR" not in txt_p1

    # Página 2: Tabela de Itens e Assinatura do Requisitante
    assert "ITEM" in txt_p2 or "DESCRIÇÃO" in txt_p2
    assert "FULANO DE TAL SILVA" in txt_p2.upper()

    # Página 3: Despacho das Autoridades
    assert "DESPACHO" in txt_p3
    assert "FISCAL ADMINISTRATIVO" in txt_p3 or "Fiscal Administrativo" in txt_p3
    assert "ORDENADOR DE DESPESAS" in txt_p3 or "Ordenador de Despesas" in txt_p3

    # Rodapé presente em todas as páginas com informação e ordem das páginas
    total_pags = len(doc)
    for idx, page in enumerate(doc):
        txt_pag = page.get_text()
        assert f"Página {idx + 1} de {total_pags}" in txt_pag
        assert "DIEx Requisitório" in txt_pag


def test_rodape_docx(tmp_path, repo):
    """Testa se o rodapé do documento Word (.docx) possui tabela com informações e numeração."""
    from app import docx_docs, regras, textos
    from conftest import payload_requisicao

    p = payload_requisicao()
    av = regras.avaliar_requisicao(p)
    doc_dict = textos.montar_requisicao(av.norm)

    caminho_docx = tmp_path / "diex_teste.docx"
    docx_docs.gerar_docx_documento(doc_dict, caminho_docx)

    assert caminho_docx.is_file()
    doc_word = Document(str(caminho_docx))
    section = doc_word.sections[0]
    footer = section.footer
    assert len(footer.tables) >= 1
    tbl_footer = footer.tables[0]
    txt_footer = tbl_footer.cell(0, 0).text
    assert "DIEx Requisitório" in txt_footer

