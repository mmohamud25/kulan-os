# Kulan OS defaults for zsh: a Mac-style prompt, history search with the
# arrow keys, easier completion, suggestions and colours as you type, power
# tools (fzf, zoxide, eza, fd, bat, ripgrep), help for missing commands, and
# Kulan Assistant (kulan ask, Alt+K after a failed command).
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
# Nicer listings with eza when it's installed: folders first, Git status.
if (( $+commands[eza] )); then
	alias ls='eza --group-directories-first'
	alias ll='eza -l --group-directories-first --git --time-style=relative'
	alias la='eza -la --group-directories-first --git --time-style=relative'
	alias tree='eza --tree --group-directories-first'
fi
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

# --- Power tools, each only when it's installed ------------------------------
# Debian names two of them differently (fd-find, bat): the usual names work.
(( $+commands[fdfind] && ! $+commands[fd] )) && alias fd='fdfind'
(( $+commands[batcat] && ! $+commands[bat] )) && alias bat='batcat'
# fzf: Ctrl+R searches your history, Ctrl+T finds a file, Alt+C goes to a folder.
if (( $+commands[fzf] )); then
	export FZF_DEFAULT_OPTS="--height=40% --layout=reverse --border=rounded --info=inline \
--color=bg+:#0f2540,fg+:#ffffff,hl:#2dd4bf,hl+:#2dd4bf,pointer:#f59e0b,marker:#f59e0b,prompt:#2dd4bf,info:#8fa0b5,border:#22344b"
	if (( $+commands[fdfind] )); then
		export FZF_DEFAULT_COMMAND='fdfind --type f --hidden --exclude .git'
		export FZF_CTRL_T_COMMAND=$FZF_DEFAULT_COMMAND
		export FZF_ALT_C_COMMAND='fdfind --type d --hidden --exclude .git'
	fi
	# Only with a line editor (a real Terminal window), as fzf's scripts expect.
	if [[ -o zle && -t 0 && -t 1 ]]; then
		for file in /usr/share/doc/fzf/examples/key-bindings.zsh /usr/share/doc/fzf/examples/completion.zsh; do
			[[ -r $file ]] && source $file
		done
		unset file
	fi
fi
# zoxide: "z project" jumps to the folder you visit most that matches.
(( $+commands[zoxide] )) && eval "$(zoxide init zsh)"

# A command that isn't installed: say which package has it (Debian's own
# package of the same name, or a known other name), or offer Kulan Assistant.
command_not_found_handler() {
	local cmd=$1 pkg=""
	local -A known=(fd fd-find bat bat rg ripgrep btop btop eza eza z zoxide python python3 pip python3-pip \
		node nodejs htop htop tree tree nvim neovim vim vim curl curl wget wget git git)
	if [[ -n ${known[$cmd]} ]]; then
		pkg=${known[$cmd]}
	elif apt-cache show --no-all-versions -- "$cmd" >/dev/null 2>&1; then
		pkg=$cmd
	fi
	print -u2 "zsh: command not found: $cmd"
	if [[ -n $pkg ]]; then
		print -u2 "  It's in the package \"$pkg\". Install it with:  sudo apt install $pkg"
	elif (( $+commands[kulan] )); then
		print -u2 "  Not sure what you need? Ask Kulan Assistant:  ask what is $cmd"
	fi
	return 127
}

# After a command fails, Alt+K asks Kulan Assistant what went wrong (it runs
# on this computer; nothing is sent anywhere).
typeset -g _kulan_last_cmd="" _kulan_last_status=0
_kulan_remember() { _kulan_last_cmd=$1 }
_kulan_status() { _kulan_last_status=$? }
add-zsh-hook preexec _kulan_remember
precmd_functions=(_kulan_status ${precmd_functions:#_kulan_status})
_kulan_ask_last() {
	if [[ -z $_kulan_last_cmd ]] || (( _kulan_last_status == 0 )); then
		zle -M "Nothing failed just now. Alt+K explains the last command that failed."
		return
	fi
	BUFFER="kulan why"
	zle accept-line
}
if (( $+commands[kulan] )); then
	zle -N _kulan_ask_last
	bindkey '^[k' _kulan_ask_last
	# Questions can end in "?": no filename matching for them.
	alias ask='noglob kulan ask'
	alias '?'='noglob kulan ask'
fi
export KULAN_LAST_FAILED_FILE="${XDG_RUNTIME_DIR:-/tmp}/kulan-last-failed-$$"
_kulan_save_failed() {
	(( _kulan_last_status != 0 )) && [[ -n $_kulan_last_cmd ]] &&
		print -r -- "$_kulan_last_status"$'\t'"$_kulan_last_cmd" >| "$KULAN_LAST_FAILED_FILE" 2>/dev/null
	return 0
}
add-zsh-hook precmd _kulan_save_failed
_kulan_cleanup() { rm -f "$KULAN_LAST_FAILED_FILE" 2>/dev/null }
add-zsh-hook zshexit _kulan_cleanup

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
