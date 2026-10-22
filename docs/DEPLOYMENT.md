# Running Lugic 24/7

The bot is one Python program that stays connected to Discord. It saves everything as files in the `data/` folder, so it needs a computer that stays on and a disk that is kept between restarts. A small rented Linux server (a VPS) fits that best, and these steps assume one running Ubuntu 22.04 or 24.04.

A free Codespace stops when you stop using it, so it is only good for testing.

## What to pick

- **A small VPS** (1 CPU and 1 GB of RAM is plenty). The disk is yours, you can copy files on and off it, and the cost is a few dollars a month. This is what the steps below use.
- **Oracle Cloud Always Free** costs nothing, but Oracle can reclaim a free machine that sits idle for a week, and a quiet bot looks idle. It also sometimes has no free machines available. It works, but check on it.
- **Railway or Fly.io** can run it from the GitHub repo, but you have to attach a paid disk for `data/`, and it is harder to copy your characters in and out.

Prices change, so check the provider's own page before you sign up.

## One-time setup

Run these on the server, logged in as an admin user (or root).

1. Install what the bot needs.

```
sudo apt update
sudo apt install -y python3-venv git
```

2. Make a user for the bot and download the code.

```
sudo useradd --system --create-home --shell /usr/sbin/nologin lugic
sudo git clone https://github.com/dopp000/lugic.git /opt/lugic
sudo chown -R lugic:lugic /opt/lugic
```

3. Install the libraries.

```
sudo -u lugic python3 -m venv /opt/lugic/venv
sudo -u lugic /opt/lugic/venv/bin/pip install -r /opt/lugic/requirements.txt
```

4. Add the bot token. Type it into the file yourself and never paste it into a chat.

```
sudo -u lugic nano /opt/lugic/.env
```

The file has one line: `DISCORD_TOKEN=your token here`. You can add `CHARACTER_SITE_URL=` with the address of the character builder website (see the README) so `/character import` can point players to it. Then lock it down:

```
sudo chmod 600 /opt/lugic/.env
```

5. Copy your characters over. In the Codespace terminal, from the repo folder:

```
scp -r data USER@SERVER_IP:/tmp/lugic-data
```

Then on the server:

```
sudo mv /tmp/lugic-data /opt/lugic/data
sudo chown -R lugic:lugic /opt/lugic/data
```

Skip this step if you want to start with no characters.

6. Run the tests once to make sure the server is set up right.

```
cd /opt/lugic && sudo -u lugic venv/bin/python -m unittest discover tests
```

7. **Stop the bot in the Codespace** (Ctrl+C in its terminal). Two copies using one token will both answer every command.

8. Start it as a service so it restarts on its own after a crash or a reboot.

```
sudo cp /opt/lugic/deploy/lugic.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lugic
```

## Checking on it

```
sudo systemctl status lugic
sudo journalctl -u lugic -f
```

The log should show "Logged in as" and "Synced N slash command(s)". Press Ctrl+C to stop watching the log. That does not stop the bot.

## Updating the bot

Push your changes to GitHub from the Codespace as usual, then on the server:

```
cd /opt/lugic
sudo -u lugic git pull
sudo -u lugic venv/bin/pip install -r requirements.txt
sudo systemctl restart lugic
```

## Backups

Characters only exist in `data/`, and it is not in git. Make a daily copy:

```
sudo mkdir -p /opt/lugic-backups
sudo crontab -e
```

Add this line, which runs at 04:00 every day and keeps the newest 14 copies:

```
0 4 * * * /opt/lugic/deploy/backup.sh
```

Copies on the same server disappear if the server does. Once in a while, download one to your own computer:

```
scp USER@SERVER_IP:/opt/lugic-backups/data_*.tar.gz .
```

To restore, stop the bot, unpack a copy into `/opt/lugic` with `tar -xzf FILE -C /opt/lugic`, fix the owner with `chown -R lugic:lugic /opt/lugic/data`, and start it again.

## If something goes wrong

- **No "Logged in as" in the log:** the token in `.env` is wrong or has spaces around it.
- **Commands answer twice:** another copy is still running, usually the Codespace.
- **Commands missing in Discord:** look for "Failed to sync commands" in the log.
- **`/character say` does nothing:** the bot needs Manage Webhooks in that channel.
