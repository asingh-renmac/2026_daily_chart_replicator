# Apply the legend-key migration to the host's store by installing the already-migrated
# copy, rather than re-deriving it here.
#
# `migrate_legend_keys.py` builds its old->new mapping by replaying data\ledger_*.csv, and
# the host has NO ledger files -- so run here it would re-file nothing and report success.
# The mapping was derived on the machine that owns the ledger, before 15.1b changed the
# phrase mapper, which is the only moment it could be derived faithfully.
#
# Installing that result is safe because the two stores were shown to be the same store:
# learned_descriptors, trusted_tickers and native_ma are byte-identical, and every key
# that differs carries an IDENTICAL label, source_descriptor and added-timestamp -- only
# the key name moves. No label is created, altered or dropped.
$src = "C:\Users\mcpdeploy\legend_labels.new.json"
$dst = "C:\Users\madz\Work\asingh\haver-chart\knowledge\legend_labels.json"

$stamp  = Get-Date -Format "yyyyMMdd-HHmmss"
$backup = "$dst.bak-$stamp"

"before      : {0} bytes  sha={1}" -f (Get-Item $dst).Length, (Get-FileHash $dst -Algorithm SHA256).Hash.Substring(0,12)
Copy-Item $dst $backup -ErrorAction Stop
"backup      : $backup"

Copy-Item $src $dst -Force -ErrorAction Stop
"after       : {0} bytes  sha={1}" -f (Get-Item $dst).Length, (Get-FileHash $dst -Algorithm SHA256).Hash.Substring(0,12)
"matches src : " + ((Get-FileHash $dst).Hash -eq (Get-FileHash $src).Hash)

$blob = Get-Content $dst -Raw | ConvertFrom-Json
"entries     : " + ($blob.PSObject.Properties | Measure-Object).Count
"stale gone  : " + (-not ($blob.PSObject.Properties.Name -contains 'GDPH@USECON|ZS(#)'))
"migrated in : " + ($blob.PSObject.Properties.Name -contains 'GDPH@USECON|ZS(YRYR%(#))')
"restore with: Copy-Item '$backup' '$dst' -Force"
