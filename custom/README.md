# custom/ — o que é seu

Esta pasta é ignorada pelo git (veja o `.gitignore`): tudo que você coloca ou edita aqui fica só na sua máquina
e **vale mais** do que o que vem em `defaults/`. Atualizar o repositório (`git pull`) nunca mexe nela.

```
custom/
├── settings.yaml        endereços do ComfyUI e do Clef, memória... (criado pela tela Ajustes)
├── clef_prompt.yaml     só o que você mudou no prompt do Clef (descrições, casos de uso)
├── workflows/           seus arquivos .json salvos no ComfyUI
│   └── NSFW/            (exemplo) subpastas valem; esta e tudo dentro dela também fica fora do git
├── registry/            ligação técnica dos SEUS workflows (nós de imagem, prompt, saída)
├── prompts/             seus prompts, por workflow
└── models.yaml          (opcional) LoRAs e modelos extras para o assistente de instalação
```

## Adicionar um workflow seu

1. No ComfyUI, salve o workflow (**Workflow → Salvar**) e copie o `.json` para `custom/workflows/`
   (pode usar subpastas).
2. No ClefFlow, aba **Workflows e prompts → Adicionar workflow**. Escolha o arquivo, confira onde entram
   as imagens e o prompt e clique em **Adicionar**.

O ClefFlow cria `custom/registry/<id>.yaml` e `custom/prompts/<id>.yaml`. Seu arquivo `.json` nunca é alterado.
Caixas e máscaras, nós a descartar e valores fixos são acrescentados à mão no registro; use os de
`defaults/registry/` como exemplo (`object_move.yaml` tem caixas, `t2i.yaml` tem nós dentro de subgraph).

## Usar uma versão sua de um padrão (por exemplo, com outro workflow)

Copie `defaults/registry/<id>.yaml` para `custom/registry/` (mesmo nome e mesmo `id`) e troque `file:` pelo seu
workflow, como `file: NSFW/Meu workflow.json`. Em `custom/prompts/<id>.yaml` ficam os seus prompts para aquele id
(comece copiando `defaults/prompts/<id>.yaml` e acrescente os seus). Tudo isso fica fora do git.

## Mudar um padrão sem perder as atualizações

- **Prompts e descrições:** edite pela interface. Só a diferença vai para `custom/`; o botão
  **Restaurar padrão** apaga a sua versão.
- **Trocar o arquivo ou os nós de um padrão:** copie `defaults/registry/<id>.yaml` para `custom/registry/`
  (mesmo nome, mesmo `id`) e edite a cópia. Se o seu workflow tem outro nome, ponha o `.json` em
  `custom/workflows/` e ajuste `file:` na cópia.

## Remover

**Remover da lista** (na própria tela) tira o workflow do ClefFlow e apaga os seus registros e prompts.
O `.json` continua em `custom/workflows/`. Para sumir de vez, apague o arquivo.
