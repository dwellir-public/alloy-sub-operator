juju remove-application alloy-sub --force --no-wait
juju deploy ./alloy-sub_ubuntu@24.04-amd64.charm
juju config alloy-sub loki-url="http://10.200.100.184:3100/loki/api/v1/push"
juju config alloy-sub loki-url="http://10.200.100.184:3100/loki/api/v1/push"
juju config alloy-sub systemd-service="polkadot.service"
juju relate alloy-sub polkadot
