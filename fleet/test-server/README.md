# Kulan Fleet test server

The real `fleet` and `fleet-admin` functions (the same files Supabase runs),
with a local PostgreSQL database, on one of your own test computers. For
trying a Kulan Fleet release (People, Lost mode…) on test computers before
it goes live. The live Kulan Fleet isn't touched. Never for real use.

Sign-in to the Kulan Fleet app works as usual, except the 6-digit code is
printed in the server's terminal instead of emailed.

## On the server computer (test computer 1)
```
curl -fsSL https://github.com/mmohamud25/kulan-os/archive/refs/heads/kulan-os-2.8.tar.gz | tar xz -C ~
cd ~/kulan-os-kulan-os-2.8/fleet/test-server
sudo ./setup.sh you@example.com
sudo ./run.sh
```
`setup.sh` prints the server address, the authority fingerprint, and how to
open the Kulan Fleet app against this server:
`KULAN_FLEET_URL=https://<address>:8443 kulan-fleet-admin`.

## On each other test computer
Trust the test authority (check its fingerprint matches the one `setup.sh`
printed), then join with the server address:
```
curl -fsSk https://<address>:8443/kulan-fleet-test-ca.crt -o /tmp/kft.crt
openssl x509 -in /tmp/kft.crt -noout -fingerprint -sha256
sudo cp /tmp/kft.crt /usr/local/share/ca-certificates/kulan-fleet-test.crt && sudo update-ca-certificates && rm /tmp/kft.crt
```
Then Kulan Settings › About › Organisation › Join an organisation, with
`https://<address>:8443/functions/v1/fleet` and a join code from the app.

## When testing ends
Each computer: leave the organisation (Kulan Settings › About ›
Organisation › Leave). Other computers:
`sudo rm /usr/local/share/ca-certificates/kulan-fleet-test.crt && sudo update-ca-certificates --fresh`.
Server computer: `sudo ./teardown.sh` (removes the database, Deno, the
certificates, the firewall rule, and PostgreSQL if setup installed it),
then `rm -rf ~/kulan-os-kulan-os-2.8`.
