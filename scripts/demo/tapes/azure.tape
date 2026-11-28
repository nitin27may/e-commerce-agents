# The one-command Azure deployment, for the README and the Azure page. Recorded from a real run, never live:
#
#   1. Deploy, saving a redacted, timestamped log (scripts/demo/README.md, "Azure clip"):
#        scripts/azure-up.sh --env eca-demo ... 2>&1 | scripts/demo/redact.sh \
#          | perl -MTime::HiRes=time -ne 'printf "%.3f\t%s", time, $_' > azure-up.log
#   2. AZURE_UP_LOG=azure-up.log REDACT_WORDS="<subscription name>,<your name>" vhs scripts/demo/tapes/azure.tape
#   3. scripts/demo/check-frames.sh docs/images/azure.gif, then tear down: scripts/azure-down.sh --env eca-demo
#
# The deploy itself replays from the log at 40x (the clip says so); the resource listing runs live, through the
# same redaction filter.

Output docs/media/azure.mp4

Set Shell "bash"
Set FontSize 15
Set FontFamily "Menlo"
Set Width 1280
Set Height 800
Set Padding 28
Set Theme "GitHub Dark"
Set TypingSpeed 35ms
Set Framerate 30
Set CursorBlink false

Hide
Type `export PS1='$ '; scripts/azure-up.sh() { scripts/demo/replay-log.sh "${AZURE_UP_LOG:-azure-up.log}" 40 1.0; }; clear` Enter
Sleep 500ms
Show

Type "# One command deploys the whole stack to your own subscription. (A recorded run, 40x speed; ids masked.)" Enter
Sleep 700ms
Type "scripts/azure-up.sh --env eca-demo" Enter
Wait+Line@180s /^\$\s*$/
Sleep 3s

Hide
Type "clear" Enter
Show
Type "# What it created: one resource group." Enter
Sleep 500ms
Type `az resource list -g rg-eca-demo --query "sort_by([].{name:name, type:type}, &type)" -o table | scripts/demo/redact.sh` Enter
Wait+Line@60s /^\$\s*$/
Sleep 4s

Hide
Type "clear" Enter
Show
Type "# The apps: everything but the frontend is internal, and idle apps scale to zero." Enter
Sleep 500ms
Type `az containerapp list -g rg-eca-demo --query "[].{app:name, status:properties.runningStatus, ingress:properties.configuration.ingress.external}" -o table | scripts/demo/redact.sh` Enter
Wait+Line@60s /^\$\s*$/
Sleep 4s
