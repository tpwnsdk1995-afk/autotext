#!/data/data/com.termux/files/usr/bin/bash
cd ~/autotext || exit 1

if ! pgrep -f "python.*server.py" > /dev/null; then
  nohup python server.py > /dev/null 2>&1 &
  sleep 2
fi

am start -a android.intent.action.VIEW -d http://127.0.0.1:8000/ > /dev/null 2>&1
