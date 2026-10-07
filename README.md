# ClefFlow

Um chat para o **ComfyUI**: você escreve o que quer (e anexa fotos, se precisar), o modelo **Clef** decide qual dos seus
workflows rodar — ou uma sequência deles — e o ClefFlow executa tudo no seu ComfyUI.

> **Feito para o Qwen-Image 2.1.** Os workflows que acompanham o projeto usam os modelos e os nós do Qwen-Image 2.1
> (edição de imagens, troca de pessoa/cabeça, mover e transferir objetos, remover fundo, gerar do zero).
> Com outros modelos o ClefFlow funciona, mas você mesmo precisa trazer os workflows (veja
> [Adicionar o seu workflow](#adicionar-o-seu-workflow)).

```
 Você ──► ClefFlow (esta pasta, porta 7860) ──► Clef    (decide e devolve probabilidades)
                      │
                      └──────────────────────► ComfyUI (executa o workflow pela API HTTP)
```

O ClefFlow **não é um plugin do ComfyUI**: é um programa à parte (Python + uma página web) que conversa com o ComfyUI
por HTTP. Ele não altera os seus workflows nem a instalação do ComfyUI, a não ser pelos custom nodes que você instalar
de propósito (passo 3).

---

## O que você precisa

| | Para quê | Observação |
|---|---|---|
| **ComfyUI** | roda os workflows | Obrigatório e **atualizado** (os nós `TextEncodeQwenImage21` e `QwenImage21Cache` são recentes). [Como atualizar](https://docs.comfy.org/installation/update_comfyui). |
| **Python 3.10+** | roda o ClefFlow | `start.bat` / `start.sh` criam o ambiente sozinhos. |
| **Placa de vídeo NVIDIA** | o Qwen-Image 2.1 | Os workflows foram feitos e usados numa RTX com 16–24 GB de VRAM (modelo em int8). |
| **Modelos e LoRAs do Qwen-Image 2.1** | os workflows carregam | O [assistente](#3-deixar-o-comfyui-pronto-assistente) baixa os que você escolher. |
| **Servidor do Clef** | escolhe o workflow | Opcional para testar a interface (há um *modo de teste*), necessário para decisões de verdade. Veja [Clef](#o-servidor-do-clef). |

## Instalação

### 1. Instalar o ComfyUI (se ainda não tem)

O ClefFlow só pede imagens ao ComfyUI, então **ele precisa estar instalado, atualizado e aberto**.
Instale o [ComfyUI Desktop](https://www.comfy.org/download) (mais simples) ou a
[versão manual](https://github.com/comfyanonymous/ComfyUI) e abra-o uma vez. O assistente do passo 3 confere isso e
mostra o que fazer se não achar.

### 2. Baixar e ligar o ClefFlow

```
git clone <endereço deste repositório> ClefFlow
```

Pode ficar em qualquer pasta. Com o ComfyUI aberto, rode `start.bat` (Windows) ou `./start.sh`. Na primeira vez ele cria
o ambiente Python e instala as dependências; depois abre <http://127.0.0.1:7860>.

### 3. Deixar o ComfyUI pronto (assistente)

Na primeira vez a tela de conversa mostra **Antes de começar**. Clique em **Ver e instalar** (ou, a qualquer hora,
**Ajustes → Nós, LoRAs e modelos → Abrir o assistente**). Ele:

1. **procura a pasta do ComfyUI** (pelo próprio ComfyUI em execução; se não achar, você informa a pasta) e, se não
   encontrar o ComfyUI, explica como instalar;
2. mostra o que **falta** em três grupos: **nós do ClefFlow** (caixas vermelha/verde e Viggle Turbo), **LoRAs** e
   **modelos base** do Qwen-Image 2.1 (arquivos grandes, desmarcados por padrão);
3. **pergunta o que você quer instalar.** Nada é copiado nem baixado antes de você clicar em *Instalar*;
4. baixa direto para `models/loras/` (ou a pasta do tipo), confere o **sha256** de cada arquivo e mostra o progresso.
   Depois de copiar nós, **reinicie o ComfyUI**.

Clicou em *Agora não*? O aviso some e o assistente continua em *Ajustes*. Se o ComfyUI está em **outro computador**, o
assistente não instala nada nele: use a lista de arquivos e as pastas indicadas para copiar à mão.

A lista de arquivos está em [`defaults/models.yaml`](defaults/models.yaml); para acrescentar os seus, crie
`custom/models.yaml` com o mesmo formato.

<details>
<summary>Instalação manual (sem o assistente)</summary>

**Modelos** (pastas normais do ComfyUI):

| Arquivo | Pasta | Onde baixar |
|---|---|---|
| `qwen_image_2.1_int8_convrot.safetensors` | `models/diffusion_models/` | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1) |
| `qwen3vl_8b_fp8_heretic.safetensors` | `models/text_encoders/` | idem |
| `qwen_image_2.1_vae_bf16.safetensors` | `models/vae/` | idem |

**LoRAs** (`models/loras/`). Cada workflow usa alguns:

| Arquivo | Usado em | Licença | Onde baixar |
|---|---|---|---|
| `Qwen-Image-2.1-viggle-turbo-v0.2.1-6step-lora-r128.safetensors` | todos os **Turbo** (6 passos) e *Mover ou trocar objetos (Quality)* | Qwen Research | [Viggle/Qwen-Image-2.1-viggle-turbo](https://huggingface.co/Viggle/Qwen-Image-2.1-viggle-turbo) |
| `bfs_body_swap_v1.0_qwen_2.1.safetensors` | trocar corpo, vários corpos, transferir objeto (Turbo) | MIT | [Alissonerdx/BFS-Best-Face-Swap](https://huggingface.co/Alissonerdx/BFS-Best-Face-Swap) |
| `bfs_head_v1.1_qwen_2.1.safetensors` | trocar cabeça | MIT | idem |
| `Qwen-Image-2.1-Natural-Exposure-LoRA-4000.safetensors` | quase todos | Qwen Research | [prithivMLmods/Qwen-Image-2.1-Natural-Exposure-LoRA](https://huggingface.co/prithivMLmods/Qwen-Image-2.1-Natural-Exposure-LoRA) |
| `qwen-image-2.1-consistency-2000.safetensors` e `qwen-image-2.1-consistency.safetensors` | edição, trocar corpo/cabeça, substituir área | Qwen Research | [ausboss/Qwen-Image-2.1-Consistency-LoRA](https://huggingface.co/ausboss/Qwen-Image-2.1-Consistency-LoRA) |
| `flymy_realism.safetensors` | edição, trocar cabeça, mover/transferir objeto | Apache-2.0 | [flymy-ai/qwen-image-realism-lora](https://huggingface.co/flymy-ai/qwen-image-realism-lora) |

Pastas dentro de `models/loras/` são aceitas: o ClefFlow acha o arquivo pelo nome.

**Nós do ClefFlow** (`comfyui_nodes/`): `install_nodes.bat` (Windows) ou `./install_nodes.sh` (Linux/macOS). O script procura o
`custom_nodes` do ComfyUI (ou aceite o caminho: `install_nodes.bat "D:\ComfyUI"`) e **não sobrescreve nada sem perguntar**.
Reinicie o ComfyUI depois.

</details>

Referência: [guia oficial do Qwen-Image 2.1 no ComfyUI](https://docs.comfy.org/tutorials/image/qwen/qwen-image-2-1).

> **Licenças.** O código do ClefFlow é MIT, mas os modelos e LoRAs **não**: cada autor tem a sua licença e vários
> (Qwen Research) são de **uso não comercial**. Confira a página de cada um antes de usar.

### 4. Conferir

1. **Ajustes → ComfyUI → Testar.** Se o endereço estiver errado, o ClefFlow tenta as portas comuns
   (8188, 8000–8003, 8199). O ComfyUI Desktop costuma usar a 8000.
2. **Ajustes → Verificar workflows.** Converte cada workflow para o formato da API do ComfyUI e confere nós, ligações,
   limites e modelos contra o **seu** ComfyUI. Cada linha vermelha diz o que falta. Faça isso uma vez antes do primeiro uso.
3. **Ajustes → Clef.** Aponte para o servidor do Clef ou use o **modo de teste** só para ver a interface funcionando.

A tela de conversa mostra um aviso *Antes de começar* enquanto o ComfyUI ou o Clef não respondem, ou faltam arquivos.

---

## Os workflows que acompanham

Ficam em `defaults/workflows/` (e `defaults/registry/` diz ao ClefFlow quais nós recebem imagem, prompt e saída).

| Workflow | O que faz | Imagens | Desenhar |
|---|---|---|---|
| Edição de imagem — Turbo / Quality | edita uma imagem a partir de uma instrução; aceita até 2 imagens de referência (opcionais) | 1–3 | — |
| Trocar o corpo — Turbo / Quality | troca a pessoa inteira da cena pela de uma referência; no Turbo, uma 3ª imagem (opcional) define o lugar | 2 (3 no Turbo) | — |
| Trocar a cabeça — Turbo / Quality | troca só o rosto/cabeça | 2 | — |
| Trocar vários corpos | uma referência para cada pessoa da cena | 2–4 | — |
| Mover ou trocar objetos — Turbo / Quality | **mover** um objeto para um destino vazio, ou **trocar** dois objetos de lugar entre si, na mesma imagem. São dois prompts do mesmo workflow; o Clef escolhe pelo seu pedido | 1 | caixa vermelha (objeto) e verde (destino, ou o outro objeto) |
| Adicionar ou substituir numa área | **adicionar** um objeto novo num lugar vazio, ou **substituir** o que já está dentro do quadrado. Dois prompts do mesmo workflow; o Clef escolhe pelo seu pedido | 1 | só um quadrado vermelho |
| Transferir objeto entre imagens — Turbo / Quality | leva um objeto ou pessoa de uma imagem para outra | 2 | caixas vermelha e verde |
| Remover fundo | deixa só o sujeito (PNG) | 1 | — |
| Gerar imagem do zero — Turbo / Quality | cria a partir de texto | 0 | — |

*Turbo* usa o LoRA viggle (6 passos, sem CFG); *Quality* usa 25 passos. **Imagens opcionais que você não manda saem do grafo** (o ComfyUI não carrega nem processa nada para elas), então não deixam o workflow mais lento. O Clef prefere o Turbo, a não ser que você peça
máxima qualidade. Workflows que precisam de caixa ou máscara **pausam** e abrem um desenho na tela (a espera é de até 15 minutos).

O **Gerar do zero (Quality)** e o **Remover fundo** são os modelos oficiais do ComfyUI (com *subgraph*). O ClefFlow
"achata" o subgraph ao converter, então eles funcionam como os outros. No Gerar do zero o reescritor de prompt embutido é
descartado: quem escreve o prompt é o Clef (ou o redator opcional dos Ajustes).

---

## Como o ClefFlow organiza os arquivos

```
ClefFlow/
├── defaults/            O que vem no repositório. O app NUNCA escreve aqui.
│   ├── workflows/       os .json dos workflows acima
│   ├── registry/        ligação técnica de cada um (nós de imagem, prompt, saída)
│   ├── prompts/         biblioteca de prompts de cada workflow
│   ├── models.yaml      LoRAs e modelos que o assistente sabe instalar
│   └── clef_prompt.yaml descrição de cada workflow para o Clef + casos de uso
├── custom/              O que é SEU. Ignorado pelo git: tudo que você edita na interface vai para cá.
│   ├── settings.yaml
│   ├── workflows/       seus .json (a subpasta NSFW/, por exemplo, também fica de fora do git)
│   ├── registry/  prompts/  clef_prompt.yaml  models.yaml
│   └── README.md
├── comfyui_nodes/       custom nodes para copiar para o ComfyUI (install_nodes)
├── clef_server/         servidor do Clef de referência
├── clefflow/  web/      o programa
└── data/                imagens enviadas, resultados e conversas (ignorado pelo git)
```

Regras simples:

- **`custom/` vale mais que `defaults/`** (mesmo id = o seu substitui o padrão).
- Ao editar um padrão pela interface, só a **diferença** vai para `custom/`. Por isso `git pull` continua trazendo
  as novidades dos padrões que você não mexeu, e **Restaurar padrão** é só apagar a sua cópia.
- `custom/` inteira é ignorada pelo git. Não precisa listar seus arquivos no `.gitignore`.
- Os arquivos `.json` nunca são alterados pelo ClefFlow: ele converte uma cópia em memória a cada execução.

### Adicionar o seu workflow

1. Salve o workflow no ComfyUI (**Workflow → Salvar**) e copie o `.json` para `custom/workflows/` (subpastas valem).
2. **Workflows e prompts → Adicionar workflow.** Escolha o arquivo, confira as entradas de imagem, o nó do prompt e
   a saída, e clique em **Adicionar**.
3. Escreva a descrição que o Clef vai ler (*o que faz e quando usar*) e os prompts. Rode **Verificar workflows**.

Fica pronto para usar. Caixas, máscaras, nós a descartar e valores fixos você acrescenta à mão em
`custom/registry/<id>.yaml` — veja `defaults/registry/object_move.yaml` (caixas) e `defaults/registry/t2i.yaml`
(nós dentro de subgraph). Detalhes em [`custom/README.md`](custom/README.md).

> Workflows e prompts com conteúdo adulto: o repositório só traz versões neutras. Guarde os seus em `custom/workflows/`
> (ou numa subpasta como `custom/workflows/NSFW/`), e os prompts em `custom/prompts/`. Um registro seu em
> `custom/registry/<id>.yaml` com o mesmo `id` de um padrão (e `file: NSFW/arquivo.json`) faz aquele id usar o seu
> workflow. Nada de `custom/` entra no git.

### Onde você mexe

| O quê | Na interface | Fica em |
|---|---|---|
| O que o Clef sabe de cada workflow, **quais prompts ele pode usar**, casos de uso | *Workflows e prompts*, *Casos de uso* | `custom/clef_prompt.yaml` |
| Prompts de cada workflow | *Workflows e prompts* → cartões de prompt | `custom/prompts/<id>.yaml` |
| Ligações técnicas (nós, widgets, descartes) | só leitura | `custom/registry/<id>.yaml` |
| Endereços, memória, redator | *Ajustes* | `custom/settings.yaml` |

Em *Workflows e prompts*, cada prompt tem **O Clef pode usar** (liga/desliga), **Padrão**, **Quando usar** (o que o Clef lê
para escolher entre os prompts) e o **texto**. `{request}` recebe o pedido do usuário; `<image1>`, `<image2>` ficam literais
para o Qwen. Marque *reescrever em inglês* para que o redator opcional (qualquer servidor compatível com a API da OpenAI)
trate o pedido antes.

Em *Casos de uso* você cria sequências fixas (ex.: trocar o corpo e depois levar a pessoa para um novo fundo), define de qual
imagem cada passo parte (`imagem N`, `resultado anterior`) e pode fixar o prompt de cada passo.

---

## Como o Clef decide

O Clef não gera texto: devolve **probabilidades**. Por isso o ClefFlow transforma cada decisão numa pergunta de
múltipla escolha:

1. **Rota**: qual workflow ou caso de uso (e, opcionalmente, qual vem depois). Acima de 26 opções, decide em dois estágios.
2. **Prompt** do workflow e **qual imagem vai em cada entrada**.
3. Se a confiança na rota fica abaixo de *Perguntar a você abaixo de* (0,45), o chat pergunta em vez de arriscar.

Cada resposta mostra **Como o Clef decidiu**, com as probabilidades. Você também pode forçar um workflow no seletor ao lado
do campo de texto.

### O servidor do Clef

Os pesos são da Cloudflare (`Cloudflare/clef-flash`, ~19 GB, e `Cloudflare/clef`, ~55 GB, Apache-2.0).
O ClefFlow fala com um servidor HTTP local:

- `POST /v1/systemone` — decide (aceita `images` em data URL ou base64);
- `GET /health` — responde `{"loaded": bool, "device": "...", "vram": {"allocated_gb": N}}`;
- `POST /v1/unload` (ou `/unload`) — tira o modelo da GPU.

Em `clef_server/` há uma implementação de referência (carrega no primeiro pedido e descarrega depois de um tempo parado):

```
cd clef_server
python -m venv .venv          # instale antes o torch com CUDA da sua placa
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python server.py --model Cloudflare/clef-flash --port 8321 --quant int8
```

Se você já tem um servidor do Clef, aponte *Endereço do servidor* e *Caminho* em **Ajustes → Clef**.

> **Sem GPU sobrando?** O `clef-flash` em BF16 ocupa ~19 GB de VRAM e disputa a GPU com o Qwen. Use `--quant int8`,
> ou deixe o ClefFlow descarregá-lo antes de gerar (abaixo).

### Memória: Clef e Qwen na mesma placa

Em **Ajustes → Clef → Descarregar o Clef antes de gerar**, o modo *Automático* mede a memória (`/system_stats` do ComfyUI +
`GET /health` do Clef) e só descarrega o Clef se `VRAM total − VRAM do Clef − folga` for menor que a VRAM que o workflow
usa (padrão: 16 GB). Também libera os modelos do ComfyUI se a VRAM livre não basta para carregar o Clef de volta.
Cada decisão aparece no chat. *Sempre* e *Nunca* forçam o comportamento. Um workflow pode ter a própria estimativa em
`registry/<id>.yaml` (`mem: {vram_gb: 12, ram_gb: 8}`).

---

## Problemas comuns

| Sintoma | O que fazer |
|---|---|
| **ComfyUI fora do ar** | Abra o ComfyUI. Em *Ajustes → ComfyUI → Testar* o ClefFlow tenta achar a porta certa. |
| **Nós não instalados: DrawBox, ViggleTurbo…** | *Ajustes → Abrir o assistente* (ou `install_nodes`) e reinicie o ComfyUI. Outros nós ausentes: atualize o ComfyUI. |
| **`'x.safetensors' não está instalado`** (aviso amarelo) | Falta baixar o arquivo, ou ele tem outro nome. O assistente mostra o que falta e baixa. |
| **Download com erro ou "indisponível"** | O autor mudou ou removeu o arquivo, ou ele exige login. Abra a *página do autor* na lista e ajuste a URL em `custom/models.yaml`. |
| **Value not in list / prompt outputs failed validation** | O ComfyUI recusou o grafo: o texto do erro cita o nó. Rode *Verificar workflows*. |
| **Travou ao rodar o Clef e o Qwen juntos** | Falta VRAM/RAM. Deixe *Descarregar o Clef antes de gerar* em *Automático* e use `--quant int8` no Clef. |
| **"Clef sem resposta"** | O servidor do Clef não está de pé ou o endereço está errado (*Ajustes → Clef*). |
| **O workflow não roda sem eu desenhar** | Normal: Mover/Substituir/Transferir pausam para você marcar a área. |
| **Mexi e quero voltar ao padrão** | *Workflows e prompts* → **Restaurar padrão**. |

O ClefFlow só escuta em `127.0.0.1` e recusa pedidos vindos de outros sites. **Não exponha a porta na rede**: não há login.

## Para quem quer mexer no código

```
pip install -r requirements-dev.txt
python -m pytest -q
```

Os testes usam um ComfyUI falso (`tests/fake_comfy.py`) com o `object_info` **real** (reduzido) de um ComfyUI com o Qwen-Image 2.1
(`tests/object_info.json`, gerado por `tests/make_object_info.py`), um Clef de teste e um servidor HTTP falso do Clef.
Provam a lógica de roteamento, a conversão para o formato de API (inclusive subgraphs), as camadas `defaults/` + `custom/`
e o fluxo completo, mas **não substituem** o botão *Verificar workflows* no seu ComfyUI real, nem testam o Clef com os pesos de verdade.

`python tests/demo_server.py` sobe a interface com dados de mentira em <http://127.0.0.1:7861>. Com `DEMO_SETUP=1` simula um ComfyUI novo (sem nós, LoRAs e modelos) e downloads lentos, para ver o assistente; com `DEMO_NO_COMFY=1`, um ComfyUI que não responde.

## Créditos

- [Qwen-Image 2.1](https://docs.comfy.org/tutorials/image/qwen/qwen-image-2-1) e os modelos para ComfyUI: Qwen e [Comfy-Org](https://huggingface.co/Comfy-Org/Qwen-Image-2.1).
- LoRAs: [Viggle](https://huggingface.co/Viggle/Qwen-Image-2.1-viggle-turbo) (turbo), [Alissonerdx](https://huggingface.co/Alissonerdx/BFS-Best-Face-Swap) (BFS body/head swap),
  [prithivMLmods](https://huggingface.co/prithivMLmods/Qwen-Image-2.1-Natural-Exposure-LoRA) (Natural Exposure),
  [ausboss](https://huggingface.co/ausboss/Qwen-Image-2.1-Consistency-LoRA) (Consistency), [flymy-ai](https://huggingface.co/flymy-ai/qwen-image-realism-lora) (realism).
- Clef: [Cloudflare](https://huggingface.co/Cloudflare/clef-flash).

## Licença

O código do ClefFlow é distribuído sob a [licença MIT](LICENSE). Os modelos, LoRAs e workflows de terceiros
mencionados acima têm as licenças dos seus autores (veja a coluna *Licença* e `defaults/models.yaml`).
