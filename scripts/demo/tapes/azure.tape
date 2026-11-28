# The one-command Azure deployment, for the README and the Azure page. Recorded from a real run, never live:
#
#   1. Deploy, saving a redacted, timestamped log (scripts/demo/README.md, "Azure clip"):
#        scripts/azure-up.sh --env eca-demo ... 2>&1 | scripts/demo/redact.sh \
#          | perl -MTime::HiRes=time -ne 'printf "%.3f\t%s", time, $_' > azure-up.log
#   2. AZURE_UP_LOG=azure-up.log REDACT_WORDS="<subscription name>,<your name>" vhs scripts/demo/tapes/azure.tape
#   3. scripts/demo/check-frames.sh docs/images/azure.gif, then tear down: scripts/azure-down.sh --env eca-demo
#
# Everything replays from the run's records: the deploy log at 40x (the clip says so), and the two listings from
# their saved output (AZ_RESOURCES, AZ_APPS: the redacted text `az resource list` and `az containerapp list` printed),
# because the deployment is torn down before the clip is made. The command typed is the command that ran.

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
Type `export PS1='$ '; scripts/azure-up.sh() { scripts/demo/replay-log.sh "${AZURE_UP_LOG:-azure-up.log}" 40 1.0; }; az() { case "$*" in *"resource list"*) cat "$AZ_RESOURCES" ;; *"containerapp list"*) cat "$AZ_APPS" ;; esac; }; clear` Enter
Sleep 500ms
Show

Type "# One command deploys the stack to your subscription. Here it builds this branch's images into a registry." Enter
Type "# (A recorded run at 40x; ids masked.)" Enter
Sleep 700ms
Type "scripts/azure-up.sh --env eca-demo --image-source acr --build-from-source" Enter
Wait+Line@180s /^\$\s*$/
Sleep 3s

Hide
Type "clear" Enter
Show
Type "# What it created: one resource group." Enter
Sleep 500ms
Type `az resource list -g rg-eca-demo --query "sort_by([].{Name:name, Type:type}, &Type)" -o table | scripts/demo/redact.sh` Enter
Wait+Line@60s /^\$\s*$/
Sleep 4s

Hide
Type "clear" Enter
Show
Type "# The apps: everything but the frontend is internal, and idle apps scale to zero." Enter
Sleep 500ms
Type `az containerapp list -g rg-eca-demo --query "[].{App:name, Status:properties.runningStatus, Public:properties.configuration.ingress.external}" -o table --only-show-errors | scripts/demo/redact.sh` Enter
Wait+Line@60s /^\$\s*$/
Sleep 4s
