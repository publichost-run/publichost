# Terminal demo for the README, recorded with VHS (https://github.com/charmbracelet/vhs):
#   vhs docs/demo.tape   ->  docs/assets/demo.gif
# Needs a running PublicHost instance; replace the host below with yours.
Output docs/assets/demo.gif
Set FontSize 18
Set Width 1100
Set Height 560
Set Theme "Catppuccin Mocha"

Hide
Type "python3 -m http.server 3000 >/dev/null 2>&1 &" Enter
Type "clear" Enter
Show

Type "ssh -p 2222 -R 0:localhost:3000 tunnel.example.com"
Sleep 500ms
Enter
Sleep 4s
Type "extend 30"
Sleep 500ms
Enter
Sleep 3s
