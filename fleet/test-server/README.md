# Kulan Fleet test server

The real `fleet` and `fleet-admin` functions (the same files Supabase runs),
with a local PostgreSQL database, on one of your own test computers. Use it
to try a Kulan Fleet release on test computers before it goes live. The live
Kulan Fleet isn't touched. Not for real use.

Signing in to the Kulan Fleet app works as usual, except that the 6-digit
code is printed in the server's terminal instead of being emailed.

## On the server computer (test computer 1)
```
curl -fsSL https://github.com/mmohamud25/kulan-os/archive/refs/heads/kulan-os-2.9.tar.gz | tar xz -C ~
cd ~/kulan-os-kulan-os-2.9/fleet/test-server
sudo ./setup.sh you@example.com
sudo ./run.sh
```
`setup.sh` prints the server address, the authority fingerprint, and how to
open the Kulan Fleet app against this server:
`KULAN_FLEET_URL=https://<address>:8443 kulan-fleet-admin`.

## Google Workspace mailboxes (2.8e)
The test server has a stand-in for Google, so you can try mailboxes with
People without a real Workspace. In the Kulan Fleet app, open Settings › Google
Workspace, paste the test key that setup.sh wrote
(`/opt/kulan-fleet-test/google-test-key.json`) and act as
`it@<your test domain>`, for example `it@acme.test`. Each change is printed in
the server's terminal. To use a real Workspace instead, start the server with
`sudo KULAN_FLEET_REAL_GOOGLE=1 ./run.sh`. This creates real mailboxes.

## On each other test computer
Trust the test authority, checking that its fingerprint matches the one
`setup.sh` printed. Then join with the server address:
```
curl -fsSk https://<address>:8443/kulan-fleet-test-ca.crt -o /tmp/kft.crt
openssl x509 -in /tmp/kft.crt -noout -fingerprint -sha256
sudo cp /tmp/kft.crt /usr/local/share/ca-certificates/kulan-fleet-test.crt && sudo update-ca-certificates && rm /tmp/kft.crt
```
Then open Kulan Settings › About › Organisation › Join an organisation, and
enter `https://<address>:8443/functions/v1/fleet` and a join code from the app.

## When testing ends
On each computer, leave the organisation in Kulan Settings › About ›
Organisation › Leave. Then, on each computer other than the server:
`sudo rm /usr/local/share/ca-certificates/kulan-fleet-test.crt && sudo update-ca-certificates --fresh`.
On the server computer, run `sudo ./teardown.sh`. It removes the database,
Deno, the certificates, the firewall rule, and PostgreSQL if setup installed
it. Then run `rm -rf ~/kulan-os-kulan-os-2.9`.
