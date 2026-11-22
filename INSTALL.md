# Installing Featherstone

This guide assumes no technical background. Setup takes about 20 minutes, and you only do it once.

## How it works

Featherstone isn't an App Store app. It runs on **your Mac**, and your **iPhone** opens it like an app from the Home Screen. A free service called **Tailscale** connects the two privately, so only your own devices can reach it, at home or away.

**You'll need:**

- **A Mac that stays on.** A desktop Mac is ideal. A laptop works if it stays plugged in and doesn't go to sleep.
- **An iPhone with iOS 16.4 or newer,** for notifications. Check in Settings → General → About → iOS Version.
- **About 20 minutes,** and the Mac and iPhone side by side.

---

## Step 1: Set up Tailscale on your Mac

1. Go to **https://tailscale.com/download/mac** and download Tailscale for Mac. The Mac App Store version also works.
2. Open it. A small Tailscale icon appears in the menu bar at the top right of your screen.
3. Click the icon and choose **Log in**. Sign in with your Google, Apple or Microsoft account; this creates your free Tailscale account.
4. When it asks, allow the VPN configuration. That's how your devices connect privately.

## Step 2: Install the app on your Mac

1. Open **Terminal**: press **⌘ Command + Space**, type `Terminal`, and press **Return**. A window with text opens.
2. Copy this whole line, paste it into the Terminal window (**⌘ Command + V**), and press **Return**:

   ```bash
   curl -fsSL https://raw.githubusercontent.com/matthewsaaronj/scripture_memorization_agent/main/install.sh | bash
   ```

3. Wait a minute or two while it works through 4 steps. Some things you might see:
   - **"This Mac needs Python 3.10 or newer":** a download page opens. Download the **macOS installer**, open it, click through with the default choices, then paste the same command into Terminal again.
   - **A link saying HTTPS or Serve needs to be enabled:** open the link, click **Enable**, then come back to Terminal. It continues on its own.
   - **"Tailscale isn't installed" or "not signed in":** finish Step 1, then paste the command again.
4. When it says **All set!**, it shows **your app's address**. It looks like `https://your-mac-name.tail1234.ts.net:8444`. Keep that window open for Step 4, or find the address later in the file `featherstone-data/address.txt` in your home folder.

## Step 3: Keep the Mac awake

The app is only reachable while the Mac is awake.

- **Desktop Mac:** open **System Settings → Energy**, and turn on **Prevent automatic sleeping when the display is off**.
- **MacBook:** keep it plugged in. In **System Settings → Battery → Options**, turn on **Prevent automatic sleeping on power adapter when the display is off**.

The app starts again by itself after a restart. You don't have to do anything.

## Step 4: Set up your iPhone

1. Install **Tailscale** from the App Store and sign in with **the same account** you used on the Mac. Allow the VPN configuration when asked.
2. Open **Safari**, not Chrome, and type in your app's address from Step 2.
3. Tap the **Share** button (the square with an arrow), scroll down, and tap **Add to Home Screen**, then **Add**.
4. Close Safari and open **Featherstone** from your Home Screen. Always open it this way from now on.
5. Tap **Settings**, then **Turn on notifications**, then **Allow**. Tap **Send a test**; a notification should arrive within a few seconds.

You're done.

---

## Using the app

- **Add:** type a reference like `Alma 34 14 15`. The colon and dash fill in as you type, and the verse text is fetched for you. New verses go into the **Backlog**.
- **Today:** what's due. Tap **Begin review** to quiz yourself.
- **The program:** each verse moves through **Daily** (7 days) → **Weekly** (4 weeks) → **Monthly** (24 months) → **Mastered**. A new verse comes in from the Backlog every 7 days by default.
- **Settings:** change how often new verses start, the review counts, notification times, and more.
  - **Settings → Connections** is optional. Add an OpenAI API key there if you'd like the app to suggest verses by theme.

## Updating to a newer version

Paste the same command from Step 2 into Terminal again. It updates the app and keeps all your verses and progress.

## If something goes wrong

| Problem | What to do |
|---|---|
| The address won't open on the iPhone | Check that Tailscale is on (it shows "Connected") on both the iPhone and the Mac, and that the Mac is awake. |
| Settings says to add the app to the Home Screen first | You opened it in Safari. Open **Featherstone** from the Home Screen instead. |
| Settings says notifications are blocked | iPhone **Settings → Notifications → Featherstone**: turn on **Allow Notifications**. |
| The test notification doesn't arrive | Open Settings in the app again (it signs the phone up again), then tap **Send a test**. |
| Anything else | Run the install command again. It's safe and fixes most problems. Error details are saved in the `featherstone-data/logs` folder in your home folder. |

## Removing it

In Terminal:

```bash
bash ~/featherstone/uninstall.sh
```

This stops the app and removes its address. Your verses stay in the `featherstone-data` folder in your home folder until you delete that folder.

---

### For the technically curious

- **Where things live:**
  - the app: `~/featherstone`
  - its Python libraries: `~/featherstone-venv`
  - your data, backups, logs and keys: `~/featherstone-data`
- **What runs:** two background jobs, `com.featherstone` (the web app on `localhost:5057`) and a 15-minute job for daily upkeep, the 3 AM text check and backup, and notifications.
- **More than one copy on the same Mac:** `install.sh --name NAME --port 5058 --https-port 8445` sets up a separate copy with its own data folder and address.
- **Optional settings:** everything has a default. `.env.example` lists what you can override.
