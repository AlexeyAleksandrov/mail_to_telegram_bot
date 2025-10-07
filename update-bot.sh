#!/bin/bash
cd ~/home/mail_to_telegram_bot
docker-compose down
git pull origin master  # если используете git
docker-compose build --no-cache
docker-compose up -d
echo "Bot updated successfully"