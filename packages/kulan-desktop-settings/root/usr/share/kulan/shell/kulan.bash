# Kulan OS defaults for bash: the same Mac-style prompt as zsh, history
# search with the arrow keys and easier completion. Loaded from ~/.bashrc;
# anything you set after that line wins.

case $- in *i*) ;; *) return 0 ;; esac

# History: shared and longer.
HISTSIZE=10000
HISTFILESIZE=20000
HISTCONTROL=ignoreboth:erasedups
shopt -s histappend
shopt -s autocd 2>/dev/null     # type a folder's name to go there
shopt -s checkwinsize

# Up/Down search the history for what you've started typing; Tab ignores
# upper and lower case and shows the choices at once.
bind '"\e[A": history-search-backward' 2>/dev/null
bind '"\e[B": history-search-forward' 2>/dev/null
bind '"\eOA": history-search-backward' 2>/dev/null
bind '"\eOB": history-search-forward' 2>/dev/null
bind 'set completion-ignore-case on' 2>/dev/null
bind 'set show-all-if-ambiguous on' 2>/dev/null
bind 'set colored-stats on' 2>/dev/null
bind 'set mark-symlinked-directories on' 2>/dev/null

if command -v dircolors >/dev/null 2>&1; then
	eval "$(dircolors -b)"
fi
alias ls='ls --color=auto'
alias ll='ls -lh --color=auto'
alias la='ls -lAh --color=auto'
alias grep='grep --color=auto'
alias diff='diff --color=auto'
alias ip='ip -color=auto'

# Prompt: you@computer folder $   (git branch inside a project; $ turns red
# when the last command failed).
__kulan_git_branch() {
	local branch
	branch="$(git symbolic-ref --short HEAD 2>/dev/null || git rev-parse --short HEAD 2>/dev/null)" || return
	[ -n "$branch" ] && printf ' \001\e[35m\002(%s)\001\e[0m\002' "$branch"
}
__kulan_prompt() {
	local status=$?
	local mark='\$'
	[ "$status" -ne 0 ] && mark='\[\e[31m\]\$\[\e[0m\]'
	PS1="\[\e[1;36m\]\u@\h\[\e[0m\] \[\e[1m\]\W\[\e[0m\]$(__kulan_git_branch) ${mark} "
}
# Tell the terminal the current folder, so new tabs open there. Loaded
# first, so the prompt below runs first and still sees the last command's
# result.
[ -r /etc/profile.d/vte-2.91.sh ] && . /etc/profile.d/vte-2.91.sh
case ";${PROMPT_COMMAND:-};" in
	*";__kulan_prompt;"*) ;;
	*) PROMPT_COMMAND="__kulan_prompt${PROMPT_COMMAND:+;$PROMPT_COMMAND}" ;;
esac
true
