# Bisq Advanced Monitor

Monitor headless para ofertas da Bisq 1 com painel de terminal e alertas no Telegram.

## Recursos

- painel `bisq-top` inspirado no `htop`, atualizado continuamente;
- ofertas ativas e pausadas, faixas em BTC e valores em BRL/EUR;
- cotações BTC/BRL e BTC/EUR obtidas da API local da Bisq;
- destaque especial para ofertas aceitas;
- detector de oportunidades no livro público BRL/EUR, comparando cada oferta
  com a cotação de mercado e aplicando limite de vantagem e anti-spam;
- histórico local dos últimos 100 eventos;
- alertas no Telegram para aceite, remoção, pausa e reativação;
- alertas de falha e recuperação do daemon/API e sincronização DAO;
- falhas transitórias de cotação ficam somente no painel, evitando spam no Telegram;
- serviços systemd de usuário com reinício automático e inicialização no login;
- verificação diária nos repositórios Arch e nos releases oficiais da Bisq,
  com validação e alertas no Telegram;
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
OPPORTUNITY_CURRENCIES=BRL,EUR
OPPORTUNITY_MIN_ADVANTAGE_PCT=1.5
OPPORTUNITY_REALERT_IMPROVEMENT_PCT=1.0
OPPORTUNITY_MAX_ALERTS_PER_CYCLE=3
OPPORTUNITY_RESET_HOURS=24
```

Uma oportunidade de compra é uma oferta `SELL` abaixo da cotação de mercado;
uma oportunidade de venda é uma oferta `BUY` acima dela. O limite padrão exige
1,5% de vantagem. Cada oferta gera um alerta e só volta a alertar se melhorar
pelo menos 1 ponto percentual, evitando mensagens repetidas. O sinal é
informativo: confirme limites, método de pagamento, taxas e detalhes da contraparte.
Uma oferta que deixa de ser oportunidade precisa permanecer ausente por 24 horas
antes de poder gerar um novo alerta.

O alerta no Telegram é enviado como relatório: ação sugerida, vantagem, lado da
ordem, preço ofertado e de mercado, limites em BTC e moeda fiduciária, método de
pagamento, horário e ID completo. Relatórios BRL permanecem somente em reais;
relatórios EUR acrescentam as conversões BTC/BRL, EUR/BRL e BRL/EUR.
Todos os relatórios incluem ainda o capital principal estimado em BTC, BRL e
EUR e o lucro/economia bruta contra o preço de mercado. O valor líquido depende
das taxas e do depósito de segurança exibidos pela Bisq antes da confirmação.

Proteja os arquivos e inicie os serviços:

```bash
chmod 600 ~/.config/bisq/daemon.env ~/.config/bisq-monitor/telegram.env
systemctl --user start bisq-daemon.service bisq-offer-monitor.service
```

Instale também a rotina de atualização do sistema:

```bash
sudo pacman -S --needed pacman-contrib
sudo ./install-updater.sh
```

A rotina verifica diariamente, entre 06:00 e 06:30, os repositórios Arch e o
release oficial da Bisq. Pacotes disponíveis no Arch são atualizados como um
conjunto coerente; depois ela reinicia os serviços, valida a API e notifica o
grupo do Telegram. Como os três componentes também podem ter sido compilados
localmente, a rotina emite um único alerta quando uma tag oficial mais nova
precisar ser compilada e instalada. Verificações sem atualização ficam
silenciosas. Timeouts de cotação continuam restritos ao `bisq-top`.

Para consultar manualmente sem atualizar:

```bash
sudo /usr/local/sbin/bisq-auto-update --check
```

Teste a comunicação com o Telegram:

```bash
sudo /usr/local/sbin/bisq-auto-update --notify-test
```

Para executar a rotina imediatamente:

```bash
sudo systemctl start bisq-auto-update.service
journalctl -u bisq-auto-update.service
```

## Uso

```bash
bisq-top
```

- `q`: sair;
- `Tab`: alternar entre oportunidades e histórico de alertas;
- `↑`/`↓` ou `j`/`k`: navegar pela seção selecionada;
- `Enter`: abrir o relatório completo da oportunidade no terminal;
- `Esc` ou `Enter`: voltar do relatório detalhado.

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
