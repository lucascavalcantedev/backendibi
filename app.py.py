from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from supabase import create_client, Client
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
import json
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
import io
import pandas as pd
from functools import wraps

load_dotenv()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# Configuração Supabase - INSIRA SEUS DADOS AQUI
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://seu-projeto.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "sua-chave-supabase")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Middleware de autenticação básico (simplificado)
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token or token != f"Bearer {os.getenv('API_KEY', 'default-key')}":
            return jsonify({'message': 'Token inválido'}), 401
        return f(*args, **kwargs)
    return decorated

# ========== ROTAS DE MEMBROS ==========
@app.route('/api/membros', methods=['GET'])
@token_required
def get_membros():
    try:
        response = supabase.table('membros').select('*').order('codigo').execute()
        return jsonify(response.data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/membros', methods=['POST'])
@token_required
def create_membro():
    try:
        data = request.json
        # Gera código automático
        ultimo = supabase.table('membros').select('codigo').order('codigo', desc=True).limit(1).execute()
        if ultimo.data:
            ultimo_num = int(ultimo.data[0]['codigo'][1:])
            novo_codigo = f"D{ultimo_num + 1:03d}"
        else:
            novo_codigo = "D001"
        
        data['codigo'] = novo_codigo
        data['data_cadastro'] = datetime.now().isoformat()
        
        response = supabase.table('membros').insert(data).execute()
        return jsonify(response.data[0])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/membros/<id>', methods=['PUT'])
@token_required
def update_membro(id):
    try:
        data = request.json
        response = supabase.table('membros').update(data).eq('id', id).execute()
        return jsonify(response.data[0])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/membros/<id>', methods=['DELETE'])
@token_required
def delete_membro(id):
    try:
        supabase.table('membros').delete().eq('id', id).execute()
        return jsonify({'message': 'Membro excluído'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/membros/exportar-excel', methods=['GET'])
@token_required
def exportar_membros_excel():
    try:
        response = supabase.table('membros').select('*').execute()
        df = pd.DataFrame(response.data)
        
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Membros')
        
        output.seek(0)
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f'membros_{datetime.now().strftime("%Y%m%d")}.xlsx'
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ========== WEBHOOK MERCADO PAGO ==========
@app.route('/api/webhook/mercadopago', methods=['POST'])
def webhook_mercadopago():
    try:
        data = request.json
        
        # Extrair informações do webhook
        transacao = {
            'id_transacao': data.get('id'),
            'valor': data.get('transaction_amount'),
            'data': data.get('date_created'),
            'nome_pagador': data.get('payer', {}).get('first_name', '') + ' ' + data.get('payer', {}).get('last_name', ''),
            'status': 'pendente',
            'tipo': None,
            'vinculado': False
        }
        
        # Tentar vincular automaticamente pelo nome
        membros = supabase.table('membros').select('*').execute()
        for membro in membros.data:
            if membro['nome_completo'].lower() in transacao['nome_pagador'].lower():
                transacao['membro_id'] = membro['id']
                transacao['codigo_membro'] = membro['codigo']
                transacao['vinculado'] = True
                break
        
        # Salvar transação
        supabase.table('transacoes').insert(transacao).execute()
        
        return jsonify({'message': 'Webhook recebido'}), 200
    except Exception as e:
        print(f"Erro webhook: {str(e)}")
        return jsonify({'error': str(e)}), 500

# ========== TRANSAÇÕES ==========
@app.route('/api/transacoes', methods=['GET'])
@token_required
def get_transacoes():
    try:
        response = supabase.table('transacoes')\
            .select('*, membros(nome_completo, codigo)')\
            .order('data', desc=True)\
            .execute()
        return jsonify(response.data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/transacoes/<id>/confirmar', methods=['POST'])
@token_required
def confirmar_transacao(id):
    try:
        data = request.json
        tipo = data.get('tipo')  # 'dizimo' ou 'oferta'
        
        # Atualizar transação
        supabase.table('transacoes')\
            .update({'status': 'confirmada', 'tipo': tipo})\
            .eq('id', id)\
            .execute()
        
        # Criar entrada no financeiro
        transacao = supabase.table('transacoes').select('*').eq('id', id).execute()
        if transacao.data:
            trans = transacao.data[0]
            entrada = {
                'data': trans['data'],
                'valor': trans['valor'],
                'tipo': tipo,
                'descricao': f'{tipo.title()} - {trans.get("codigo_membro", "Não vinculado")}',
                'membro_id': trans.get('membro_id'),
                'transacao_id': id
            }
            supabase.table('entradas').insert(entrada).execute()
        
        return jsonify({'message': 'Transação confirmada'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ========== DESPESAS ==========
@app.route('/api/despesas/tipos', methods=['GET'])
@token_required
def get_tipos_despesa():
    try:
        response = supabase.table('tipos_despesa').select('*').execute()
        return jsonify(response.data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/despesas', methods=['GET'])
@token_required
def get_despesas():
    try:
        mes = request.args.get('mes')
        ano = request.args.get('ano')
        
        query = supabase.table('despesas').select('*, tipos_despesa(nome)')
        
        if mes and ano:
            start_date = f"{ano}-{mes}-01"
            end_date = f"{ano}-{int(mes)+1 if int(mes) < 12 else ano+1}-01"
            query = query.gte('data', start_date).lt('data', end_date)
        
        response = query.order('data', desc=True).execute()
        return jsonify(response.data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/despesas', methods=['POST'])
@token_required
def create_despesa():
    try:
        data = request.json
        response = supabase.table('despesas').insert(data).execute()
        return jsonify(response.data[0])
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ========== RELATÓRIOS PDF ==========
@app.route('/api/relatorios/entradas', methods=['GET'])
@token_required
def relatorio_entradas():
    try:
        mes = request.args.get('mes')
        ano = request.args.get('ano')
        
        # Buscar entradas do mês
        start_date = f"{ano}-{mes}-01"
        end_date = f"{ano}-{int(mes)+1 if int(mes) < 12 else ano+1}-01"
        
        entradas = supabase.table('entradas')\
            .select('*, membros(codigo)')\
            .gte('data', start_date)\
            .lt('data', end_date)\
            .execute()
        
        # Gerar PDF
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter)
        elements = []
        
        styles = getSampleStyleSheet()
        elements.append(Paragraph(f"Relatório de Entradas - {mes}/{ano}", styles['Title']))
        elements.append(Spacer(1, 0.25*inch))
        
        # Tabela de dados
        data = [['Data', 'Código', 'Tipo', 'Valor']]
        total = 0
        
        for entrada in entradas.data:
            data.append([
                entrada['data'][:10],
                entrada['membros']['codigo'] if entrada['membros'] else 'N/A',
                entrada['tipo'],
                f"R$ {float(entrada['valor']):.2f}"
            ])
            total += float(entrada['valor'])
        
        data.append(['', '', 'TOTAL:', f"R$ {total:.2f}"])
        
        table = Table(data)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -2), colors.beige),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
        ]))
        
        elements.append(table)
        doc.build(elements)
        
        buffer.seek(0)
        return send_file(
            buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'entradas_{mes}_{ano}.pdf'
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/relatorios/final-mensal', methods=['GET'])
@token_required
def relatorio_final_mensal():
    try:
        mes = request.args.get('mes')
        ano = request.args.get('ano')
        saldo_anterior = float(request.args.get('saldo_anterior', 0))
        
        # Cálculos
        start_date = f"{ano}-{mes}-01"
        end_date = f"{ano}-{int(mes)+1 if int(mes) < 12 else ano+1}-01"
        
        # Entradas
        entradas = supabase.table('entradas')\
            .select('valor')\
            .gte('data', start_date)\
            .lt('data', end_date)\
            .execute()
        
        total_entradas = sum(float(e['valor']) for e in entradas.data)
        
        # Despesas
        despesas = supabase.table('despesas')\
            .select('*')\
            .gte('data', start_date)\
            .lt('data', end_date)\
            .execute()
        
        total_despesas = sum(float(d['valor']) for d in despesas.data)
        
        # Cálculo final
        total_entradas_final = saldo_anterior + total_entradas
        saldo_transportar = total_entradas_final - total_despesas
        
        # Gerar PDF
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter)
        elements = []
        
        styles = getSampleStyleSheet()
        elements.append(Paragraph(f"Relatório Financeiro Mensal - {mes}/{ano}", styles['Title']))
        elements.append(Spacer(1, 0.25*inch))
        
        # Resumo
        resumo_data = [
            ['Saldo Anterior:', f"R$ {saldo_anterior:.2f}"],
            ['Total de Entradas:', f"R$ {total_entradas:.2f}"],
            ['Total de Entradas Final:', f"R$ {total_entradas_final:.2f}"],
            ['Total de Saídas:', f"R$ {total_despesas:.2f}"],
            ['SALDO A TRANSPORTAR:', f"R$ {saldo_transportar:.2f}"]
        ]
        
        resumo_table = Table(resumo_data, colWidths=[200, 100])
        resumo_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, -1), (-1, -1), 14),
            ('BACKGROUND', (0, -1), (-1, -1), colors.yellow),
        ]))
        
        elements.append(resumo_table)
        elements.append(Spacer(1, 0.5*inch))
        
        # Detalhamento despesas
        if despesas.data:
            elements.append(Paragraph("Detalhamento de Despesas:", styles['Heading2']))
            despesas_data = [['Data', 'Descrição', 'Valor']]
            
            for despesa in despesas.data:
                despesas_data.append([
                    despesa['data'][:10],
                    despesa['descricao'],
                    f"R$ {float(despesa['valor']):.2f}"
                ])
            
            despesas_table = Table(despesas_data)
            despesas_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
                ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ]))
            elements.append(despesas_table)
        
        doc.build(elements)
        buffer.seek(0)
        
        return send_file(
            buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'relatorio_final_{mes}_{ano}.pdf'
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ========== DASHBOARD ==========
@app.route('/api/dashboard', methods=['GET'])
@token_required
def dashboard():
    try:
        hoje = datetime.now()
        inicio_mes = hoje.replace(day=1).isoformat()
        
        # Entradas do mês
        entradas = supabase.table('entradas')\
            .select('valor, tipo')\
            .gte('data', inicio_mes)\
            .execute()
        
        total_entradas = sum(float(e['valor']) for e in entradas.data)
        dizimos = sum(float(e['valor']) for e in entradas.data if e['tipo'] == 'dizimo')
        ofertas = sum(float(e['valor']) for e in entradas.data if e['tipo'] == 'oferta')
        
        # Despesas do mês
        despesas = supabase.table('despesas')\
            .select('valor')\
            .gte('data', inicio_mes)\
            .execute()
        
        total_despesas = sum(float(d['valor']) for d in despesas.data)
        
        # Transações pendentes
        pendentes = supabase.table('transacoes')\
            .select('*', count='exact')\
            .eq('status', 'pendente')\
            .execute()
        
        # Total membros
        membros = supabase.table('membros')\
            .select('*', count='exact')\
            .execute()
        
        return jsonify({
            'total_entradas': total_entradas,
            'dizimos': dizimos,
            'ofertas': ofertas,
            'total_despesas': total_despesas,
            'transacoes_pendentes': pendentes.count,
            'total_membros': membros.count,
            'saldo_atual': total_entradas - total_despesas
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ========== LOGIN SIMPLIFICADO ==========
@app.route('/api/login', methods=['POST'])
def login():
    try:
        data = request.json
        # Verificar credenciais - MODIFIQUE AQUI COM SEU USUÁRIO E SENHA
        if data.get('username') == 'admin' and data.get('password') == 'admin123':
            return jsonify({
                'token': os.getenv('API_KEY', 'default-key'),
                'user': {'username': 'admin', 'role': 'admin'}
            })
        return jsonify({'error': 'Credenciais inválidas'}), 401
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)