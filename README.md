# Barber Manager

Sistema de gerenciamento de barbearia desenvolvido com Flask e SQLite. A aplicação
já oferece gestão operacional e um primeiro fluxo de autoatendimento para clientes.

## Requisitos

- Python 3.11 ou superior
- PowerShell no Windows

## Configuração inicial

Na pasta do projeto, crie e ative o ambiente virtual e instale as dependências:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Configure uma chave secreta para a sessão do Flask no terminal atual:

```powershell
$env:BARBER_SECRET_KEY = python -c "import secrets; print(secrets.token_hex(32))"
```

Crie o banco de dados e inicie o servidor:

```powershell
flask --app app init-db
$env:FLASK_DEBUG = "1"
flask --app app run
```

Abra `http://127.0.0.1:5000` no navegador. O banco SQLite será salvo em
`dados/barber_manager.sqlite3`.

## Testes

```powershell
python -m unittest discover -s testes
```

## Organização

- `app.py`: fábrica da aplicação Flask e comando de inicialização do banco.
- `config.py`: configuração do sistema e localização do arquivo SQLite.
- `banco_de_dados/`: conexão SQLite e esquema relacional.
- `modelos/`: modelos de dados.
- `rotas/`: rotas Flask organizadas em blueprints.
- `paginas/`: páginas HTML renderizadas pelo Flask.
- `arquivos_estaticos/`: CSS, JavaScript e imagens.
- `testes/`: testes automatizados.
- `dados/`: banco SQLite e dados locais da aplicação.

## Autoatendimento do cliente

A página inicial (`/`) apresenta a plataforma e direciona os visitantes para os
fluxos de acesso. O cliente pode criar uma conta em `/cadastro/cliente`, entrar no
painel, escolher um barbeiro e um serviço e solicitar um horário futuro. A agenda
impede conflitos para o mesmo barbeiro, e o cliente pode cancelar os próprios
atendimentos futuros.

Empresários podem cadastrar uma barbearia em `/cadastro/barbearia`; o painel
permite atualizar endereço, coordenadas e telefone, gerenciar barbeiros,
serviços, preços e agendamentos vinculados ao estabelecimento. O cadastro inclui
um catálogo demonstrativo de quatro serviços com fotos e preços iniciais, que
podem ser ajustados no painel. No formulário de serviço, o proprietário pode
enviar uma foto JPG, PNG ou WEBP de até 5 MB ou informar uma URL HTTPS. As fotos
enviadas são armazenadas localmente em `dados/uploads/catalogo/` e aparecem no
catálogo dos clientes e na lista de gestão.

Clientes podem navegar em `/barbearias`, ver o catálogo, solicitar horários e
abrir uma rota do Google Maps para as coordenadas da barbearia. O mapa e o GPS
do Google precisam de conexão com a internet; navegação offline não é oferecida.
Cadastros com localização aproximada são identificados publicamente até que o
proprietário atualize e confirme o ponto exato.

Para criar uma conta de demonstração, use o cadastro de barbearia e defina uma
senha própria. Não compartilhe nem reutilize senhas de produção em ambiente de
teste.
