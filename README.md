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
| `bfs_body_swap_v1.0_qwen_2.1.safetensors` | trocar pessoas (corpo), transferir objeto (Turbo) | MIT | [Alissonerdx/BFS-Best-Face-Swap](https://huggingface.co/Alissonerdx/BFS-Best-Face-Swap) |
| `bfs_head_v1.1_qwen_2.1.safetensors` | trocar pessoas (cabeça) | MIT | idem |
| `Qwen-Image-2.1-Natural-Exposure-LoRA-4000.safetensors` | quase todos | Qwen Research | [prithivMLmods/Qwen-Image-2.1-Natural-Exposure-LoRA](https://huggingface.co/prithivMLmods/Qwen-Image-2.1-Natural-Exposure-LoRA) |
| `qwen-image-2.1-consistency.safetensors` (a versão final; a de 2000 passos não é mais usada) | edição, trocar pessoas, substituir área | Qwen Research | [ausboss/Qwen-Image-2.1-Consistency-LoRA](https://huggingface.co/ausboss/Qwen-Image-2.1-Consistency-LoRA) |
| `qwen-image-2.1-outfit-swap.safetensors` | trocar roupa ou tatuagem (não usar com a consistência) | ver a página | página no [catálogo](defaults/models.yaml) |
| `qwen-image-2.1-restore.safetensors` | restaurar ou melhorar a qualidade de uma foto (prompts "Restaurar foto"; não usar com a consistência) | Qwen Research | [ausboss/Qwen-Image-2.1-Restore-LoRA](https://huggingface.co/ausboss/Qwen-Image-2.1-Restore-LoRA) |
| `Qwen2.1_viewpoint_orbit.safetensors` | girar o ponto de vista | ver a página | página no [catálogo](defaults/models.yaml) |
| `qwen-image-2.1-outpaint-v2.safetensors` | expandir a foto e completar áreas cinza (prompt *Expandir ou completar áreas cinza*) | Qwen Research | [ausboss/Qwen-Image-2.1-Outpaint-LoRA](https://huggingface.co/ausboss/Qwen-Image-2.1-Outpaint-LoRA) |
| `QI2.1_AnyAngle.safetensors` | mudar o ângulo da câmera com 3D (ambiente e pessoa) | Apache-2.0 | [lilylilith/QI_2.1_AnyAngle](https://huggingface.co/lilylilith/QI_2.1_AnyAngle) |
| `casual_snapshot_realism_qwen21.safetensors` | foto com cara de celular (opcional) | ver a página | só no tensor.art: baixe à mão e ponha em `models/loras/` |
| `flymy_realism.safetensors` | edição, trocar cabeça, mover/transferir objeto | Apache-2.0 | [flymy-ai/qwen-image-realism-lora](https://huggingface.co/flymy-ai/qwen-image-realism-lora) |
| `qwen-image-2.1纠正背景透视溶图v2.0.safetensors` (palavra de ativação `pysj666`) | *Colar e fundir* (perspectiva e luz) | ver a página | [RunningHubAI/rh-qwen-image-2.1v2.0-lora](https://huggingface.co/RunningHubAI/rh-qwen-image-2.1v2.0-lora) |

Pastas dentro de `models/loras/` são aceitas: o ClefFlow acha o arquivo pelo nome.

**3D (mudar o ângulo da câmera):** o TripoSplat usa `triposplat_fp16.safetensors` (`models/diffusion_models/`),
`dino_v3_vit_h.safetensors` (`models/clip_vision/`), `triposplat_vae_decoder_fp16.safetensors` e `flux2-vae.safetensors`
(`models/vae/`), todos de [VAST-AI/TripoSplat](https://huggingface.co/VAST-AI/TripoSplat) (MIT), e o
`birefnet.safetensors` (`models/background_removal/`) de [Comfy-Org/BiRefNet](https://huggingface.co/Comfy-Org/BiRefNet) (MIT),
que recorta a pessoa. Os nós (`TripoSplat…`, `SplatToFile3D`, `SaveGLB`, `RemoveBackground`) vêm no ComfyUI atualizado.
O assistente baixa tudo isso.

**Nó de outro autor:** o recorte do *Colar e fundir* usa os nodes `SAM3Segment` (pessoas) e `RMBG` (objetos) do pacote [ComfyUI-RMBG](https://github.com/1038lab/ComfyUI-RMBG); o SAM3 baixa sozinho o modelo `sam3.pt` (~3,4 GB) para `models/sam3/` no primeiro uso. O ClefFlow não o instala: o assistente mostra se falta; instale pelo ComfyUI Manager e reinicie o ComfyUI. (`Create Layered Image` e `Add Layer`, do mesmo fluxo, já vêm no ComfyUI atualizado.)

**Nós do ClefFlow** (`comfyui_nodes/`): `install_nodes.bat` (Windows) ou `./install_nodes.sh` (Linux/macOS). O script procura o
`custom_nodes` do ComfyUI (ou aceite o caminho: `install_nodes.bat "D:\ComfyUI"`) e **não sobrescreve nada sem perguntar**.
Reinicie o ComfyUI depois.

</details>

Referência: [guia oficial do Qwen-Image 2.1 no ComfyUI](https://docs.comfy.org/tutorials/image/qwen/qwen-image-2-1).

> **Licenças.** O código do ClefFlow é MIT, mas os modelos e LoRAs **não**: cada autor tem a sua licença e vários
> (Qwen Research) são de **uso não comercial**. Confira a página de cada um antes de usar.

### 4. Conferir

1. **Ajustes → ComfyUI → Testar.** Mostra na hora *Conectado* (com versão e placa) ou o motivo da falha. Se o endereço estiver errado, o ClefFlow tenta as portas comuns
   (8188, 8000–8003, 8199). O ComfyUI Desktop costuma usar a 8000.
2. **Ajustes → Verificar workflows.** Converte cada workflow para o formato da API do ComfyUI e confere nós, ligações,
   limites e modelos contra o **seu** ComfyUI. Cada linha vermelha diz o que falta. Faça isso uma vez antes do primeiro uso.
3. **Ajustes → Clef.** Aponte para o servidor do Clef ou use o **modo de teste** só para ver a interface funcionando.

A tela de conversa mostra um aviso *Antes de começar* enquanto o ComfyUI ou o Clef não respondem, ou faltam arquivos.

**Idioma.** A interface abre em **inglês**; *Settings → Language* troca para português (vale na hora, a página recarrega).
O idioma vale para as telas e para as mensagens do ClefFlow. As descrições, os critérios e as instruções que o **Clef lê**
(descrição de workflow, "quando usar", instruções gerais, perguntas) estão em **inglês** nos arquivos de `defaults/` e nos
editores, e as perguntas montadas pelo código também; os nomes de tela (títulos, rótulos) seguem o idioma da interface.
Os pedidos podem ser escritos em qualquer um dos dois idiomas.

---

## Os workflows que acompanham

Ficam em `defaults/workflows/` (e `defaults/registry/` diz ao ClefFlow quais nós recebem imagem, prompt e saída).

| Workflow | O que faz | Imagens | Desenhar |
|---|---|---|---|
| **Editar** — Turbo / Quality | **o workflow único de edição de pessoas e imagens**: edição livre, mudar luz ou estilo, **trocar pessoas** (1 a 7, só o corpo, só a cabeça ou os dois, da esquerda para a direita), **trocar roupa ou tatuagem**, **girar o ponto de vista** de um objeto, com até **8 imagens** (as que você não manda saem do grafo). O Clef escolhe o prompt **e as LoRAs** conforme o pedido (veja *LoRAs* e *Um prompt, vários casos*) | 1–8 | — |
| Mover ou trocar objetos — Turbo / Quality | **mover** um objeto para um destino vazio, ou **trocar** dois objetos de lugar entre si, na mesma imagem. São dois prompts do mesmo workflow; o Clef escolhe pelo seu pedido. **Conferido pelos pixels:** depois de *mover*, o ClefFlow olha se a caixa vermelha mudou; se o objeto ficou lá também (dois iguais), roda uma segunda passada que apaga a cópia | 1 | caixa vermelha (objeto) e verde (destino, ou o outro objeto) |
| Colar e fundir (caso de uso, 3 passos) | cola uma pessoa ou objeto (2ª imagem) num cenário (1ª imagem): **recorta sozinho só a pessoa ou o objeto que o pedido citou** (o Clef escolhe o nome numa lista, como cadeira, sofá, cachorro ou vaso, e a cor quando o pedido diz; o SAM3 procura "beige chair" e pega só aquela coisa, mesmo com outras em volta; para uma foto de produto num fundo liso, tira o fundo inteiro), com fundo transparente. **A ordem das imagens não importa**: o Clef decide pelos papéis *Ambiente* e *Pessoa ou objeto a colar* (pessoa: o SAM3 e o RMBG rodam juntos e o ClefFlow soma as máscaras, então braços, mãos, pés e pele à mostra que o SAM3 deixaria de fora voltam, sem levar cadeira ou objetos encostados); você confere o recorte e, se não ficou bom, **desenha uma caixa no original** (e/ou troca pessoa/objeto) para refazer; depois **arrasta, redimensiona e gira** no chat (pode deixar parte para fora da imagem; os cantos mantêm a proporção, as alças do meio dos lados esticam e a **bolinha acima do meio da borda superior gira**; Shift prende de 15 em 15 graus). A **fusão completa o corpo quando falta**: o Clef olha a colagem e o pedido e, se a pessoa colada veio cortada (só da cintura para cima, sem pernas ou mãos) ou o pedido manda uma pose ou ação ("sentada na cadeira, segurando uma cerveja"), a etapa Fundir vira uma edição completa (desenha o que falta, a pose e a interação com o móvel, mantendo rosto e roupa); se a pessoa já está inteira, só funde. Com **várias caixas** no recorte você recorta **vários objetos de uma vez** e posiciona cada um e por fim **funde** perspectiva, luz e sombra. Também há as variantes *a pessoa vem primeiro* e *só colar* | 2 | revisar o recorte (caixa para refazer) e posicionar a pessoa |
| Adicionar, substituir ou remover numa área | **adicionar** um objeto novo num lugar vazio, **substituir** o que está dentro do quadrado ou **remover**. Pode ter **vários quadrados vermelhos**, só para objetos **iguais** (clique num quadrado e use o **×** do canto para tirar; para objetos diferentes, uma edição de cada vez). O Clef escolhe o prompt pelo seu pedido | 1 | quadrados vermelhos |
| Transferir objeto entre imagens — Turbo / Quality | leva um objeto ou pessoa de uma imagem para outra | 2 | caixas vermelha e verde |
| Transferir **vários** objetos — Turbo / Quality | leva 2 a 4 objetos, de imagens diferentes, para uma imagem de destino: o Clef conta quantos o pedido manda, decide qual imagem é o destino e quais são objetos; na tela você desenha **uma caixa verde para cada objeto** e escolhe, em cada uma, **qual imagem enviada vai ali** (1, 2, 3… na ordem em que anexou), com uma caixa vermelha opcional em cada objeto | 3–5 | caixas verdes numeradas (cada uma com sua imagem) e vermelhas |
| Remover fundo | deixa só o sujeito (PNG) | 1 | — |
| **Ambiente em 3D** (mudar o ângulo) | a foto de um quarto, sala ou lugar vira um **modelo 3D (Gaussian Splat, TripoSplat)** que aparece na conversa: você **gira a câmera com o mouse** (para os lados, para cima, para baixo, aproxima), **baixa o PNG daquele ângulo**, baixa o `.spz` e escolhe entre ficar com o render (o cinza é o que a foto não mostrava) ou **completar o cinza**: o render segue para o Editar com a LoRA de Outpaint (só completar o cinza, sem aumentar a tela), e se sobrar mais de 2% de cinza ele roda de novo, até 2 vezes | 1 | girar o 3D |
| **Mudar o ângulo da pessoa (3D)** (caso de uso, 2 passos) | a pessoa (sem o fundo, recortada pelo BiRefNet) vira 3D, você escolhe o ângulo no mesmo visualizador e o **AnyAngle** refaz a foto nesse ângulo, mantendo a pessoa, a roupa e o cenário | 1 | girar o 3D |
| **Mudar o ângulo da pessoa e corrigir o rosto (3D + troca)** (caso de uso, 3 passos) | igual ao anterior e, no fim, **troca a cabeça (ou o corpo) pela da 2ª imagem**, para o rosto não sair degradado do 3D. O Clef decide quem é a foto e quem é a referência e se troca só a cabeça (referência com rosto/cabeça) ou também o corpo | 2 | girar o 3D |
| Gerar imagem do zero — Turbo / Quality | cria a partir de texto (o Turbo usa o mesmo grafo do Editar, sem imagens) | 0 | — |

**Controles do 3D (como no Blender).** Modo **Navegar**, o viewport do Blender em volta de um ponto central: botão do meio gira (o esquerdo também, para quem usa touchpad; Alt+esquerdo emula o do meio), Shift+arrastar ou o botão direito arrasta a vista, Ctrl+arrastar e a roda dão zoom (a roda aproxima na direção do mouse), **duplo clique** (ou Alt+clique do meio) faz do ponto clicado o centro do giro, e o teclado numérico (ou a fileira de números) troca as vistas: 1 frente, 3 direita, 7 cima (Ctrl = lado oposto), 2/4/6/8 giram 15°, Shift+4/6 inclinam, 9 o outro lado, 0 ou Home a câmera da foto, + e - zoom, "." centro no meio da cena. Modo **Andar** (o Walk do Blender, Shift+`): W A S D andam enquanto seguradas, Q/E descem e sobem, Shift corre, Alt anda devagar, arrastar olha em volta, a roda muda a velocidade e Enter volta a Navegar. A **Lente** muda o campo de visão; os atalhos ficam em *Atalhos* no painel.

**3D salvos.** Cada 3D gerado fica em `data/splats/` com a foto de origem: **a mesma foto não é gerada de novo** (o TripoSplat leva uns 30 s). Na tela do 3D, *Guardar na biblioteca* dá um nome e o mantém para sempre; os não guardados ficam só os 12 mais recentes. O botão **3D salvos**, ao lado de *Anexar imagem*, lista todos: *Abrir* (girar e baixar o PNG/o `.spz`), *Usar na conversa* (anexa a foto e já escolhe o fluxo 3D, sem gerar de novo) e *Apagar*. Um resultado que veio do 3D também tem o botão *Abrir o 3D*.

**Expandir a foto (Outpaint).** O prompt *Expandir ou completar áreas cinza* do Editar usa a LoRA de Outpaint: o Clef escolhe para onde a foto cresce (lados, em volta, cima e baixo, um lado só) e o ClefFlow aumenta a tela com cinza `#808080` (múltiplo de 32) antes de mandar; no fim cola a foto original de volta, com a borda suavizada, para o que já existia ficar idêntico. Com *Só completar o cinza* ele não aumenta nada: serve para os buracos cinza do render do 3D.

Cada workflow tem dois **modos**: *Rápido* (LoRA viggle, 6 passos) e *Qualidade* (25 passos). No seletor ao lado do campo de texto aparece só o nome do workflow e, se quiser fixar, *Nome — Rápido* ou *Nome — Qualidade*; *Automático* deixa o Clef escolher (Rápido, a não ser que você peça máxima qualidade). **Imagens opcionais que você não manda saem do grafo** (o ComfyUI não carrega nem processa nada para elas), então não deixam o workflow mais lento. Workflows que precisam de caixa ou máscara **pausam** e abrem um desenho na tela (a espera é de até 15 minutos).

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
│   ├── loras.yaml       biblioteca de LoRAs (descrição, força, prompt padrão)
│   ├── models.yaml      LoRAs e modelos que o assistente sabe instalar
│   └── clef_prompt.yaml descrição de cada workflow para o Clef + casos de uso
├── custom/              O que é SEU. Ignorado pelo git: tudo que você edita na interface vai para cá.
│   ├── settings.yaml
│   ├── loras.yaml       as suas LoRAs (e ajustes nas padrão)
│   ├── workflows/       seus .json (a subpasta NSFW/, por exemplo, também fica de fora do git)
│   ├── registry/  prompts/  clef_prompt.yaml  models.yaml
│   └── README.md
├── addons/              Extensões SUAS (código, fluxos, telas). Ignorado pelo git. Veja "Addons".
├── comfyui_nodes/       custom nodes para copiar para o ComfyUI (install_nodes)
├── clef_server/         servidor do Clef de referência
├── clefflow/  web/      o programa
└── data/                imagens enviadas, resultados e conversas (ignorado pelo git)
```

Regras simples:

- **`custom/` vale mais que `addons/`, que vale mais que `defaults/`** (mesmo id = o seu substitui o padrão).
- Ao editar um padrão pela interface, só a **diferença** vai para `custom/`. Por isso `git pull` continua trazendo
  as novidades dos padrões que você não mexeu, e **Restaurar padrão** é só apagar a sua cópia.
- `custom/` inteira é ignorada pelo git. Não precisa listar seus arquivos no `.gitignore`.
- Os arquivos `.json` nunca são alterados pelo ClefFlow: ele converte uma cópia em memória a cada execução.

### LoRAs: um workflow, várias funções

Em vez de um workflow por LoRA (corpo, cabeça, vários corpos…), o **Editar** é um só e a **cadeia de LoRAs é montada a
cada pedido**:

```
pilha fixa do workflow  →  LoRAs do prompt escolhido  →  LoRAs que o Clef acrescentou
(realismo, consistência)   (ex.: trocar_pessoas liga bfs_body)   (conforme o pedido e as descrições)
```

Cada LoRA vive na aba **LoRAs** (`defaults/loras.yaml` + `custom/loras.yaml`) com:

- **Descrição**: é o que o Clef lê para decidir se liga. Você pode reescrever.
- **Força** e **força do texto**; 0 desliga.
- **Prompt padrão**: a instrução que sempre acompanha a LoRA (ex.: `body_swap: …`); pode ser um por modo.
- **Muda outras LoRAs / ajustes do workflow**: ex. ao ligar a troca de corpo, a de realismo cai a 0 e a resolução sobe.
- **Grupo** (só uma por grupo, ex.: *troca*), **mínimo de imagens**, e se o **Clef pode acrescentar sozinho**.
- **Conflitos**: ids de LoRAs que não funcionam junto com esta (vale nos dois sentidos). Ao ligar uma, a outra é desligada
  e o chat avisa; uma LoRA que só o Clef sugeriu nunca derruba uma que o prompt escolhido já usa. Ex.: *Trocar roupa ou
  tatuagem* × *Consistência*.

O limite de LoRAs que o Clef acrescenta por pedido é `clef.max_loras` (padrão 2). Cada LoRA candidata vira uma pergunta
(*usar / não usar*) com a descrição, então o resultado aparece em *Como o Clef decidiu*.

**Montagem do prompt.** Sem LLM: o ClefFlow junta o texto do prompt escolhido, as instruções das LoRAs que o Clef
acrescentou e o seu pedido. No texto do prompt, `{loras}` marca onde entram as instruções de **todas** as LoRAs ligadas e
`{request}` onde entra o pedido. Com o **redator** ativado (Ajustes), uma LLM sua recebe esse rascunho, as descrições das
LoRAs e o pedido, e devolve o prompt final (modo *ajustar*; o modo *traduzir* só passa o pedido para inglês). Se o redator
falhar, vale o rascunho.

**Modos e ajustes.** Em `registry/<id>.yaml`, `modes:` troca arquivo, saídas e cadeia por modo; `knobs:` dá nome aos
parâmetros que LoRAs e prompts podem mudar (`resolution`, `scene_mp`, `steps`…, com `off` para ignorar um nó). Rota fixa:
`edit@quality`. Em casos de uso, cada passo aceita `mode:` e `loras:`.

**Acrescentar a sua LoRA.** Aba *LoRAs* → **Nova LoRA** (ou clique num arquivo que o ComfyUI tem e ainda não foi
descrito). Escreva a descrição; se quiser, um prompt padrão e “o Clef pode acrescentar sozinho”. Para ela entrar sempre
em certo prompt, ponha o id em *LoRAs e ajustes deste prompt* (`id`, `id=0.6`, `id=0.6@turbo`). Uma LoRA sua pode ter conteúdo
adulto: ela e seus prompts ficam só em `custom/`.

**Baixar uma LoRA pelo link do Hugging Face.** Aba *LoRAs* → **Baixar do Hugging Face**: cole o link da página do modelo
(ou de um arquivo `.safetensors`), escolha o arquivo e clique em baixar. Ele vai para `models/loras/` do ComfyUI e já fica
cadastrado na biblioteca (escreva a descrição depois). Repositórios restritos ou com licença a aceitar pedem uma chave:
crie um token de leitura em <https://huggingface.co/settings/tokens>, aceite a licença na página do modelo e escreva
`HF_TOKEN=seu_token` num arquivo **`.env`** na pasta do ClefFlow (há um modelo em `.env.example`; o git ignora o `.env`).
Reinicie o ClefFlow. A chave só é enviada ao Hugging Face.

### Levar alguém ou algo de uma imagem para outra: transferir × colar

Duas imagens e "ponha isto naquela foto" pode ir por dois caminhos, e eles servem a coisas diferentes (testado com gerações
reais, 31 pedidos):

| O pedido | Caminho | Por quê |
|---|---|---|
| **No lugar de** alguém ou algo ("o coringa no lugar do cara de vermelho", "troque o cavalo pelo peão") | **Transferir objeto** | redesenha dentro da caixa verde e tira o que estava lá; a colagem deixava o antigo por baixo |
| Alguém **segura** ou usa um objeto da outra imagem ("faça o batman segurar a tesoura") | **Transferir objeto** | refaz a mão segurando; o editor geral inventava outra tesoura |
| **Acrescentar** uma pessoa num lugar, mesmo com pose nova ("o batman sentado no banco", "a Frieren na praia") | **Colar e fundir** | você escolhe lugar e tamanho, o rosto fica igual e a fusão completa o corpo e a pose |
| Pôr um **objeto** numa superfície ("a tesoura em cima da mesa") | **Colar e fundir** | fica idêntico; o transferir também funciona |
| Copiar estilo, trocar só o rosto ou a roupa | **Editor** | |

Como o Clef escolhe: com duas imagens, quando a rota cai entre transferir, colar, fundir ou o editor, ele responde uma
**pergunta curta** ("a coisa trazida substitui algo, é segurada, é acrescentada ou é outra edição?"), que acertou 28 de 29
frases nos testes (a escolha entre todas as rotas errava "no lugar de" e "segurar"). A resposta também diz ao transferir se
o que está na caixa verde **sai** ou **fica**. Na colagem, se o recorte da pessoa encosta na borda da foto (um busto, sem
pernas), a fusão passa sozinha a **completar o corpo**. Imagens com fundo transparente vão ao ComfyUI sobre branco (o ComfyUI
as deixaria pretas).

### Como o Clef entende casos de várias imagens

1. **O que há em cada imagem.** Com 2 ou mais imagens, a primeira pergunta ao Clef é, para cada uma, *pessoa, lugar sem
   pessoas, objeto ou outro*. O rótulo vai junto de todas as perguntas seguintes, então o usuário não precisa dizer
   “cena” ou “novo fundo”. (Desligue em `clef.analyze_images: false`.)
2. **Quantidade exata.** Cada caso de uso declara `images:` (ex.: *Colar e fundir* = 2, *Trocar a pessoa e o fundo* = 3);
   com outra quantidade ele nem é oferecido ao Clef. Isso tira a confusão entre os dois.
3. **Segunda olhada.** Se a rota ficar abaixo de `clef.refine_below` (0,6), o Clef compara só os 3 candidatos mais
   prováveis, já sabendo quantas imagens há e o que mostram. Só então, se ainda houver dúvida (`clef.ask_below`), pergunta a você.
4. **Papéis.** Um caso de uso pode declarar `roles:` (ex.: cena, pessoa, fundo); o Clef responde uma pergunta por papel
   (“qual imagem é a pessoa de referência?”), respeita “a pessoa da imagem 3”, “o ambiente é a imagem 1” e nunca usa a mesma
   imagem em dois papéis. Os passos usam `role:<papel>` nas entradas; sem certeza, vale a ordem de envio.

### Um prompt, vários casos (variáveis)

Um prompt pode declarar `vars`: perguntas de múltipla escolha que o Clef responde (ele não escreve texto; o ClefFlow monta
o texto com a resposta, deixando a sua decisão explícita no prompt final). Há três tipos:

- `count` — quantas pessoas/itens. O prompt **Trocar pessoas** troca as primeiras N pessoas da imagem 1, **da esquerda
  para a direita**, pelas pessoas das imagens seguintes (imagem 2, 3, 4…). Se você manda 4 imagens, o Clef pode responder
  de 1 a 3; nunca mais do que as imagens que existem.
- `choice` — uma opção entre várias, cada uma com seu texto, LoRAs e ajustes. Em *Trocar pessoas*: **corpo e cabeça**
  (padrão), **só o corpo** ou **só a cabeça**, e **com ou sem imagem de lugar**. Em *Trocar roupa ou tatuagem*: roupa,
  tatuagens ou os dois.
- `angle` — giro de câmera para a LoRA de órbita (*Girar o ponto de vista*): lido do pedido (“90 graus para a direita”,
  “de costas”, “de cima”). Se o pedido não traz o ângulo, abre uma **tela com a imagem** para escolher lado, ângulo
  (45, 90, 135, 180) e altura. Funciona melhor com objetos.

No texto use `{chave}`; contagens também dão `{chave_map}` (quem vira quem), `{chave_refs}`, `{chave_next}` e `{chave_many}`
(o `many_text` da variável, que só entra com **mais de uma** pessoa). Por isso, com **uma pessoa** o prompt de *Trocar
pessoas* fica só com as instruções das LoRAs (`body_swap: …` e/ou `head_swap: …`, conforme o que trocar; em corpo e cabeça
as duas, com a LoRA de cabeça em 1); o mapa “1ª pessoa da esquerda…” e a frase “Replicate the exact pose…” só aparecem com
várias pessoas. Com
`slots: order` as imagens entram nas entradas na ordem em que foram enviadas. As variáveis são editadas no cartão do
prompt, em *Workflows e prompts*.

### Meu ou oficial? Onde cada coisa vai

Tudo que você cria ou edita **pela interface** (casos de uso, LoRAs, prompts, descrições, ajustes, casos do agente, workflows
importados) é gravado em `custom/`, e `custom/`, `addons/`, `data/` e `.env` ficam **fora do git**: nada disso é publicado
por engano, e quem baixa o projeto começa só com o que está em `defaults/`.

Para que algo seja **oficial** (todo mundo que baixar recebe), coloque-o em `defaults/` à mão e faça o commit; a interface
nunca escreve ali:

| O quê | Onde fica em `defaults/` |
|---|---|
| Caso de uso | `clef_prompt.yaml`, lista `recipes` (copie o bloco de `custom/clef_prompt.yaml`) |
| LoRA | `loras.yaml` + `models.yaml` (para o assistente baixar) + linha na tabela do README |
| Prompt | `prompts/<workflow>.yaml` |
| Workflow | `workflows/*.json` + `registry/<id>.yaml` + `prompts/<id>.yaml` + descrição em `clef_prompt.yaml` |

Depois de copiar, **apague o item de `custom/`** (o seu vale mais que o padrão e esconderia a versão oficial) e rode
`python -m pytest -q`: o teste `test_defaults_ship_no_personal_or_nsfw_text` barra texto pessoal ou adulto em `defaults/`,
e textos novos de tela precisam entrar em `web/locales/en.json` (`python tests/uitexts.py` lista o que falta).
O que o Clef lê (descrições, `when`, perguntas) vai em inglês.

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

### Addons: funcionalidades suas

Quer mudar a interface, ajudar o prompt com o seu LLM, criar um fluxo novo (por exemplo, girar a imagem) ou ligar uma
API sua? Crie um **addon** em `addons/<nome>/`. A pasta é ignorada pelo git, então você mexe à vontade e o `git pull`
nunca encosta nela.

```
python -m clefflow addon new meu-addon      # ou Ajustes > Addons > Criar addon; reinicie o ClefFlow
```

Um addon pode trazer **workflows, registros, prompts e LoRAs** (mesmo formato de `defaults/`), **código Python**
(`addon.py`: ganchos para o pedido e o prompt, **ações** = fluxos feitos em Python sem ComfyUI, rotas, o **LLM** de
Ajustes, dados) e **interface** (`web/main.js` com `window.ClefFlow`: botões, telas, janelas, ajustes). O modelo criado
já vem com um exemplo de cada. A referência completa está em [`addons/README.md`](addons/README.md), e a skill
`clefflow-addons` (`.claude/skills/`) ensina o Claude Code a criar e alterar addons para você.

### Agente: casos de uso que o próprio ClefFlow cria

O Clef só escolhe entre fluxos e casos de uso que já existem (mais uma etapa extra depois). Um pedido como "deixe a
pessoa da imagem 2 colorida e com melhor qualidade, depois coloque-a no ambiente da imagem 1" precisa de uma sequência
própria, com a imagem 2 passando por uma etapa **antes** de entrar no recorte e na fusão. Para isso existe o agente
(**Ajustes > Agente de casos de uso**, desligado por padrão; usa o LLM do redator):

1. Depois de escolher a rota, o Clef responde uma pergunta extra: o plano cobre o pedido inteiro ou falta uma etapa?
   Só com "falta" (e confiança acima de `agent.gap_min`) o agente acorda. Também dá para chamá-lo na mão no seletor
   de workflow da conversa ("Agente: montar um caso de uso novo").
2. O LLM trabalha em rodadas, como um agente: consulta o catálogo (workflows, prompts, LoRAs, casos existentes), pergunta ao
   Clef o que depende de ver as imagens, manda rascunhos e recebe de volta os erros até entregar um caso válido.
3. O caso passa pelas mesmas regras da tela de Casos de uso (workflows, entradas, prompts, modos e LoRAs existentes, número
   de imagens, passos no máximo). Um passo pode ter **instrução própria** (`request`): "Colorize this photo...", em vez
   de receber o pedido inteiro.
4. Travas: limite de respostas do LLM e de consultas ao Clef por criação; caso com exatamente os mesmos passos de um existente é
   reaproveitado; o id é sempre inédito (nunca sobrescreve); você confirma antes de guardar (`agent.confirm`); os criados
   ficam **em teste** em `custom/generated_recipes.yaml` (fora do git) e o mais antigo sai passando de `agent.max_trial`, a não
   ser que você clique em **Manter**; ao chegar em `agent.max_generated` mantidos o agente para.

**Casos de uso repetidos.** Em Casos de uso, **Verificar repetições** pede ao Clef uma nota de 0 a 100% para cada par (mesmo
caso, parecidos ou diferentes, somada à comparação dos passos) e mostra os pares do mais ao menos repetido. **Fundir os
dois** (ou o grupo) pede ao LLM um único caso mais geral, em geral usando papéis no lugar da ordem fixa das imagens. Você
revisa a proposta; só depois de **Aplicar a fusão** algo é gravado, e os originais são desativados (ou apagados, se você
escolher).

### Onde você mexe

| O quê | Na interface | Fica em |
|---|---|---|
| O que o Clef sabe de cada workflow, **quais prompts ele pode usar**, casos de uso | *Workflows e prompts*, *Casos de uso* | `custom/clef_prompt.yaml` |
| Prompts de cada workflow | *Workflows e prompts* → cartões de prompt | `custom/prompts/<id>.yaml` |
| Ligações técnicas (nós, widgets, descartes) | só leitura | `custom/registry/<id>.yaml` |
| LoRAs: descrição, força, prompt padrão | aba *LoRAs* | `custom/loras.yaml` |
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
   A etapa seguinte só entra com confiança de pelo menos 50% (`clef.then_min`), depois de dar 1,5× de peso a "nenhuma" (`clef.then_none_weight`), e só entre workflows que cabem nas imagens
   enviadas: ela recebe o resultado no lugar de uma das fotos, então com uma foto só não aparece "transferir entre imagens".
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

### Redator de prompt (LLM opcional)

Em *Ajustes > Redator de prompt* você cadastra **vários provedores** (OpenAI, OpenRouter, Google Gemini, Anthropic, DeepSeek,
Groq, um servidor local ou qualquer endereço compatível com a API de chat da OpenAI) e, em cada um, **vários modelos**;
um seletor diz qual está em uso (o mesmo LLM serve ao agente de casos de uso e aos addons).

- **Chave de API:** cole no campo e clique em *Guardar chave*. Ela vai para o arquivo `.env` desta pasta (variável
  `LLM_API_KEY_<id do provedor>`; o git ignora o arquivo) e **nunca** para `custom/settings.yaml`; a tela só sabe se ela
  existe. Se preferir, escreva a linha no `.env` e reinicie. Servidor local não precisa de chave.
- **Testar** manda uma pergunta de uma palavra e mostra o tempo ou o motivo da falha (chave errada, endereço, modelo).
- **"Enxerga imagens"** (por modelo): o ClefFlow manda junto as imagens do passo (reduzidas), e o redator descreve pelo que vê
  ("o homem de jaqueta vermelha à direita") em vez de depender de "imagem 1". Só marque em modelos com visão.
- O redator só age nos prompts marcados com `rewrite` (hoje, a **fusão que completa o corpo**). Se falhar, o ClefFlow segue com
  o prompt montado sem ele e **avisa o motivo na conversa**.
- Quem já tinha um endereço e um modelo salvos continua funcionando: viram o provedor "Padrão" (chave em `LLM_API_KEY`).

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

**Tradução da interface.** O texto-fonte é português (`web/app.js`, mensagens do servidor, nomes em `defaults/`);
`web/i18n.js` traduz o que aparece na tela com o catálogo `web/locales/en.json` (`strings` para texto fixo, `templates`
com `{}` para as partes variáveis). Campos editáveis e o que está em `translate="no"` (o pedido do usuário) não são
tocados. Texto novo de interface precisa de uma linha no catálogo: `python tests/uitexts.py` lista o que falta e o teste
`test_catalog_covers_every_interface_text` acusa.

`python tests/demo_server.py` sobe a interface com dados de mentira em <http://127.0.0.1:7861> (com `DEMO_ADDON=1`, já com um addon criado do modelo; com `DEMO_SPLAT=caminho/arquivo.spz`, o passo 3D devolve esse splat de verdade). Com `DEMO_SETUP=1` simula um ComfyUI novo (sem nós, LoRAs e modelos) e downloads lentos, para ver o assistente; com `DEMO_NO_COMFY=1`, um ComfyUI que não responde.

## Créditos

- [Qwen-Image 2.1](https://docs.comfy.org/tutorials/image/qwen/qwen-image-2-1) e os modelos para ComfyUI: Qwen e [Comfy-Org](https://huggingface.co/Comfy-Org/Qwen-Image-2.1).
- LoRAs: [Viggle](https://huggingface.co/Viggle/Qwen-Image-2.1-viggle-turbo) (turbo), [Alissonerdx](https://huggingface.co/Alissonerdx/BFS-Best-Face-Swap) (BFS body/head swap),
  [prithivMLmods](https://huggingface.co/prithivMLmods/Qwen-Image-2.1-Natural-Exposure-LoRA) (Natural Exposure),
  [ausboss](https://huggingface.co/ausboss/Qwen-Image-2.1-Consistency-LoRA) (Consistency), [flymy-ai](https://huggingface.co/flymy-ai/qwen-image-realism-lora) (realism).
- 3D: [TripoSplat](https://huggingface.co/VAST-AI/TripoSplat) (VAST-AI), [BiRefNet](https://huggingface.co/Comfy-Org/BiRefNet); LoRAs [AnyAngle](https://huggingface.co/lilylilith/QI_2.1_AnyAngle) e [Outpaint](https://huggingface.co/ausboss/Qwen-Image-2.1-Outpaint-LoRA). O visualizador usa [three.js](https://threejs.org) e [Spark](https://sparkjs.dev) (MIT, em `web/vendor/`).
- Clef: [Cloudflare](https://huggingface.co/Cloudflare/clef-flash).

## Licença

O código do ClefFlow é distribuído sob a [licença MIT](LICENSE). Os modelos, LoRAs e workflows de terceiros
mencionados acima têm as licenças dos seus autores (veja a coluna *Licença* e `defaults/models.yaml`).
