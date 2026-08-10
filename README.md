# Bisq Advanced Monitor

Monitor headless para ofertas da Bisq 1 com painel de terminal e alertas no Telegram.

## Recursos

- painel `bisq-top` inspirado no `htop`, atualizado continuamente;
- ofertas ativas e pausadas, faixas em BTC e valores em BRL/EUR;
- cotações BTC/BRL e BTC/EUR obtidas da API local da Bisq;
- destaque especial para ofertas aceitas;
- histórico local dos últimos 100 eventos;
- alertas no Telegram para aceite, remoção, pausa e reativação;
- alertas de falha e recuperação do daemon/API e sincronização DAO;
- falhas transitórias de cotação ficam somente no painel, evitando spam no Telegram;
- serviços systemd de usuário com reinício automático e inicialização no login;
- credenciais mantidas fora do repositório.

## Requisitos

- Linux com systemd de usuário;
- Python 3 com `curses`;
- Bisq 1 headless (`bisq-daemon` e `bisq-cli` em `/usr/bin`);
- bot e grupo do Telegram.

## Instalação

```bash
git clone https://github.com/ArthurBogiano/bisq-advanced-monitor.git
cd bisq-advanced-monitor
./install.sh
```

Configure uma senha longa em `~/.config/bisq/daemon.env`:

```dotenv
BISQ_API_PASSWORD=uma_senha_longa_e_aleatoria
BISQ_API_PORT=9998
```

Configure o Telegram em `~/.config/bisq-monitor/telegram.env`:

```dotenv
TELEGRAM_BOT_TOKEN=token_fornecido_pelo_BotFather
TELEGRAM_CHAT_ID=-1000000000000
BISQ_API_PORT=9998
```

Proteja os arquivos e inicie os serviços:

```bash
chmod 600 ~/.config/bisq/daemon.env ~/.config/bisq-monitor/telegram.env
systemctl --user start bisq-daemon.service bisq-offer-monitor.service
```

## Uso

```bash
bisq-top
```

- `q`: sair;
- `↑` e `↓`: navegar pelo histórico de alertas.

Para consultar a API diretamente:

```bash
bisqctl getmyoffers --direction=sell --currency-code=BRL
```

Ao abrir a interface gráfica com `bisq-gui`, o daemon é interrompido temporariamente para evitar acesso concorrente ao mesmo diretório de dados e reiniciado ao sair.

## Segurança

Nunca versione `daemon.env`, `telegram.env`, carteiras, bancos, diretórios Tor ou o arquivo de estado. O `.gitignore` bloqueia esses caminhos. O monitor mostra somente o sufixo dos IDs das ofertas no painel e nas notificações.

## Estrutura

```text
bin/       painel e utilitários
config/    modelos de configuração sem segredos
src/       coletor e notificações
systemd/   serviços de usuário
```
