# Kulan OS defaults for zsh: a Mac-style prompt, history search with the
# arrow keys, easier completion, suggestions and colours as you type.
# Loaded from ~/.zshrc; anything you set after that line wins.

[[ -o interactive ]] || return 0

# --- History ----------------------------------------------------------------
HISTFILE=${HISTFILE:-$HOME/.zsh_history}
HISTSIZE=10000
SAVEHIST=10000
setopt append_history inc_append_history share_history
setopt hist_ignore_dups hist_ignore_space hist_reduce_blanks hist_verify

# --- Typing -----------------------------------------------------------------
setopt auto_cd                 # type a folder's name to go there
setopt interactive_comments    # allow "# comments" at the prompt
setopt no_beep
bindkey -e                     # Emacs keys, as in macOS Terminal

# Up/Down search the history for what you've started typing.
autoload -Uz up-line-or-beginning-search down-line-or-beginning-search
zle -N up-line-or-beginning-search
zle -N down-line-or-beginning-search
for key in '^[[A' '^[OA' "${terminfo[kcuu1]:-}"; do [[ -n $key ]] && bindkey "$key" up-line-or-beginning-search; done
for key in '^[[B' '^[OB' "${terminfo[kcud1]:-}"; do [[ -n $key ]] && bindkey "$key" down-line-or-beginning-search; done
unset key
# Home, End, Delete, and Ctrl+Left/Right by word.
bindkey '^[[H' beginning-of-line '^[OH' beginning-of-line '^[[1~' beginning-of-line
bindkey '^[[F' end-of-line '^[OF' end-of-line '^[[4~' end-of-line
bindkey '^[[3~' delete-char
bindkey '^[[1;5D' backward-word '^[[1;5C' forward-word

# --- Completion ---------------------------------------------------------------
autoload -Uz compinit
compinit -d "${XDG_CACHE_HOME:-$HOME/.cache}/zcompdump-$ZSH_VERSION"
zstyle ':completion:*' menu select
zstyle ':completion:*' matcher-list 'm:{a-zA-Z}={A-Za-z}' 'r:|[._-]=* r:|=*'
zstyle ':completion:*' group-name ''
zstyle ':completion:*:descriptions' format '%F{8}%d%f'

# --- Colours ----------------------------------------------------------------
if command -v dircolors >/dev/null 2>&1; then
	eval "$(dircolors -b)"
	zstyle ':completion:*' list-colors "${(s.:.)LS_COLORS}"
fi
alias ls='ls --color=auto'
alias ll='ls -lh --color=auto'
alias la='ls -lAh --color=auto'
alias grep='grep --color=auto'
alias diff='diff --color=auto'
alias ip='ip -color=auto'

# --- Prompt: you@computer folder %   (git branch inside a project; % turns red
# when the last command failed) ------------------------------------------------
autoload -Uz vcs_info add-zsh-hook
zstyle ':vcs_info:*' enable git
zstyle ':vcs_info:git:*' formats ' %F{5}(%b)%f'
zstyle ':vcs_info:git:*' actionformats ' %F{5}(%b|%a)%f'
add-zsh-hook precmd vcs_info
setopt prompt_subst
PROMPT='%B%F{6}%n@%m%f%b %B%1~%b${vcs_info_msg_0_} %(?.%#.%F{1}%#%f) '
RPROMPT=''

# Tell the terminal the current folder, so new tabs open there.
[[ -r /etc/profile.d/vte-2.91.sh ]] && source /etc/profile.d/vte-2.91.sh

# --- Suggestions (grey, accept with →) and colours as you type ----------------
if [[ -r /usr/share/zsh-autosuggestions/zsh-autosuggestions.zsh ]]; then
	ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE='fg=8'
	ZSH_AUTOSUGGEST_STRATEGY=(history completion)
	source /usr/share/zsh-autosuggestions/zsh-autosuggestions.zsh
fi
# Must be last.
[[ -r /usr/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh ]] &&
	source /usr/share/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh
true
