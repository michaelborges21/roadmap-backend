# Guia de Configuração e Uso do uv — Ubuntu (zsh)

Guia completo e sequencial de configuração, boas práticas, gerenciamento de projetos e solução de conflitos utilizando o **uv** no terminal Zsh.

---

## 1. Instalação do uv

O uv é instalado através de script standalone que baixa o binário e já adiciona o caminho ao seu `PATH`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Recarregue o terminal para carregar as alterações:

```bash
source ~/.zshrc
uv --version
```

---

## 2. Autocomplete no Zsh

Configure o autocompletar para o Zsh para acelerar a digitação de comandos:

```bash
echo 'eval "$(uv generate-shell-completion zsh)"' >> ~/.zshrc
source ~/.zshrc
```

---

## 3. Gerenciamento de Versões do Python

O uv gerencia e baixa builds standalone do Python independentes do sistema operacional:

```bash
# Instalar versões desejadas
uv python install 3.11 3.12 3.13

# Listar versões instaladas e disponíveis
uv python list

# ativando o ambeinte
source ~/.venvs/<ambient>/bin/activate  
```

---

## 4. Ambientes Virtuais Centralizados ("Estilo Mamba / Conda")

Por padrão, o uv cria ambientes locais (`.venv`) na raiz de cada projeto. Para emular o comportamento do `mamba` ou `conda` (onde os ambientes ficam centralizados por nome), utilizamos um diretório comum em `~/.venvs`:

```bash
mkdir -p ~/.venvs
uv venv ~/.venvs/ml-projeto --python 3.11
uv venv ~/.venvs/roadmap --python 3.13
```

### Funções no `~/.zshrc` (`uva`, `uvc`, `uvr`)

Para criar, ativar e executar comandos diretamente nesses ambientes por nome, inclua no seu `~/.zshrc`:

```zsh
# Ativar ambiente por nome: uva <nome>
uva() {
  if [[ -z "$1" ]]; then
    echo "Uso: uva <nome_do_ambiente>"
    ls ~/.venvs 2>/dev/null
    return 1
  fi
  source ~/.venvs/"$1"/bin/activate
}

# Criar e ativar ambiente por nome: uvc <nome> [versao_python]
uvc() {
  uv venv ~/.venvs/"$1" --python "${2:-3.12}"
  source ~/.venvs/"$1"/bin/activate
}

# Executar scripts usando o ambiente virtual ativo no terminal
uvr() {
  uv run --active "$@"
}
```

Recarregue o arquivo para disponibilizar as funções:

```bash
source ~/.zshrc
```

Exemplos de uso:

```bash
uvc roadmap 3.13    # Cria ~/.venvs/roadmap com Python 3.13 e já o ativa
uva roadmap         # Ativa o ambiente ~/.venvs/roadmap existente
uvr main.py         # Executa o script respeitando o ambiente ativo
deactivate          # Desativa o ambiente atual
```

---

## 5. Criação e Estruturação de Projetos

### Projetos Empacotáveis vs. Tradicionais (`--no-package`)

Nas versões modernas do uv (0.5+ / 0.12+):

- **Modo Empacotável / Distribuível (`uv init` padrão ou `uv init --app` / `uv init --lib`):**
  Configura o projeto como um **pacote instalável**, criando a pasta `src/`:
  ```text
  meu-projeto/
  ├── pyproject.toml
  └── src/
      └── meu_projeto/
          └── __init__.py
  ```
  O `pyproject.toml` inclui a seção `[build-system]` e mapeia pontos de entrada em `[project.scripts]`.

- **Modo Tradicional / Script Direto (`uv init --no-package`):**
  Ideal para scripts simples e automações, sem a pasta `src/` e sem empacotamento:
  ```bash
  uv init --no-package
  ```
  Estrutura gerada:
  ```text
  meu-projeto/
  ├── pyproject.toml
  └── main.py
  ```

---

### Dúvidas Frequentes de Estrutura

1. **Por que o `__init__.py` do pacote não veio vazio?**  
   Por conveniência, o uv insere uma função `main()` no `src/pacote/__init__.py` e cria um atalho de comando em `[project.scripts]`, permitindo rodar `uv run <nome_do_pacote>` imediatamente.

2. **Como organizar submódulos dentro de `src/`?**  
   Mantenha módulos com responsabilidades separadas:
   ```text
   src/
   └── todo/
       ├── __init__.py       # Pode ficar vazio ou exportar símbolos públicos
       ├── main.py           # Ponto de entrada da aplicação
       ├── database.py       # Acesso a banco de dados
       └── models.py         # Classes e modelos de dados
   ```

3. **Resolução de Imports:**  
   Como o uv instala o pacote local em modo editável (`editable mode`), utilize imports absolutos baseados no nome do pacote:
   ```python
   from todo.database import Database
   from todo.models import Task
   ```

4. **Como adotar `main.py` mantendo a pasta `src/`:**  
   - Esvazie `src/todo/__init__.py`.
   - Crie `src/todo/main.py`.
   - Atualize `pyproject.toml`:
     ```toml
     [project.scripts]
     todo = "todo.main:main"
     ```

5. **Como migrar de projeto empacotável (`src/`) para tradicional (`main.py` na raiz):**  
   - Crie `main.py` na raiz e remova `src/`:
     ```bash
     rm -rf src
     ```
   - No `pyproject.toml`, remova as seções `[build-system]` e `[project.scripts]`.

---

## 6. Gerenciamento de Dependências e Execução

### Instalar Pacotes

- **Diretamente no ambiente ativo (estilo pip tradicional):**
  ```bash
  uv pip install numpy pandas
  ```
- **Declarativo pelo projeto (`pyproject.toml`):**
  ```bash
  uv add requests fastapi
  ```

### Formas de Rodar o Projeto

1. **Pelo atalho do ambiente ativo (`uvr`):**
   ```bash
   uvr main.py
   ```
2. **Pelo comando uv com a flag explícita:**
   ```bash
   uv run --active main.py
   ```
3. **Pelo script registrado no `pyproject.toml` (para projetos empacotáveis):**
   ```bash
   uv run todo
   ```
4. **Executando como módulo:**
   ```bash
   uv run python -m todo.main
   ```
5. **Diretamente pelo Python especificando o `PYTHONPATH`:**
   ```bash
   PYTHONPATH=src python3 -m todo.main
   ```

---

## 7. Solução de Problemas e Conflitos de Ambiente

### Conflito: `VIRTUAL_ENV does not match the project environment path .venv`

#### O Problema
Ao rodar `uv run main.py`, o terminal emite o aviso:
```text
warning: `VIRTUAL_ENV=/home/mborges/.venvs/roadmap` does not match the project environment path `.venv` and will be ignored; use `--active` to target the active environment instead
```

#### Causa
O uv prioriza o ambiente local `.venv` existente na raiz do projeto. Se você já tiver ativado um ambiente centralizado no terminal (por exemplo, via `uva roadmap` gerando a variável `VIRTUAL_ENV=/home/mborges/.venvs/roadmap`), o uv alerta que ignorará o ambiente ativo para usar o `.venv` do projeto.

#### Soluções Disponíveis

- **Solução 1: Utilizar a flag `--active` ou a função `uvr` (Configurada no projeto)**
  1. Remove-se o diretório `.venv` local para evitar duplicidade:
     ```bash
     rm -rf .venv
     ```
  2. Executa-se com o atalho `uvr`:
     ```bash
     uvr main.py
     ```
     O uv direcionará as dependências e a execução diretamente para o ambiente ativo (`~/.venvs/roadmap`).

- **Solução 2: Link Simbólico (`.venv -> ~/.venvs/<nome>`)**  
  Se preferir continuar usando o comando padrão `uv run` sem flags adicionais:
  1. Garanta que as versões de Python coincidam (em `pyproject.toml` e no venv central).
  2. Remova o `.venv` local antigo:
     ```bash
     rm -rf .venv
     ```
  3. Crie um symlink apontando para o ambiente central:
     ```bash
     ln -s ~/.venvs/roadmap .venv
     ```
  Dessa forma, tanto o uv quanto o terminal apontarão para a mesma pasta física.

---

### Erro: `No interpreter found for Python 3.12`

#### Causa
Configuração legada onde a variável `UV_PYTHON_PREFERENCE=only-system` forçava o uv a buscar apenas interpretadores do sistema, impedindo o download gerenciado.

#### Correção
Definir a preferência por versões gerenciadas pelo próprio uv no `~/.zshrc`:
```bash
export UV_PYTHON_PREFERENCE=only-managed
```

---

## 8. Manutenção, Limpeza e Remoção

### Remover Interpretador Gerenciado

```bash
uv python list
uv python uninstall 3.12
```
> [!WARNING]
> Ambientes virtuais baseados nesse interpretador precisarão ser recriados se o binário base for removido.

### Remover Apenas um Ambiente Centralizado

```bash
rm -rf ~/.venvs/roadmap
```

### Limpar Cache de Pacotes

```bash
uv cache clean
```

### Desinstalação Completa do uv

Para remover o uv e todas as suas configurações do sistema:

1. Remover ambientes e interpretadores:
   ```bash
   rm -rf ~/.venvs
   rm -rf "$(uv python dir)"
   rm -rf "$(uv cache dir)"
   rm -rf ~/.local/share/uv ~/.config/uv ~/.cache/uv
   ```
2. Remover binários:
   ```bash
   rm -f ~/.local/bin/uv ~/.local/bin/uvx
   ```
3. Limpar entradas no `~/.zshrc` relacionadas ao uv (`uva`, `uvc`, `uvr`, completion).
