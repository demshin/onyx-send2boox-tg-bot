# What it is?

Python code to handle files Onyx Boox e-book reader via Send2Boox service.

** This is gian-didom personal fork of the original project. **
This fork solves a series of problems I faced with the original project, such as:
- The files being pushed to the cloud, but with a NaN:Nan timestamp, which makes the files not appear in the reader.
- The fix of the oss2 multipart upload library, which reliles on the permissions to read the already uploaded file parts (not available with the SST token)
- The double dot in the file extension
- The extension being properly set, instead of the .txt used by default

## Still to fix
The files appear on the tables, but need to be downloaded again because the first download fails.

# Usage

## How to get token

First you need to have a token:

1. Edit "config.ini" file and add your e-mail address there.
2. Run "request_verification_code.py" script to request e-mail with verification code.
3. Check mail -- you should get e-mail from "【ONYX BOOX】 <info@mail.boox.com>"
   with 6 digit code inside.
4. Run "obtain_token.py 6_digit_code" script -- it will login to send2boox
   service and store token into "config.ini" file.

## Sending files to e-reader

Run "send_file.py FILENAME1 FILENAME2" script. It uses token from "config.ini"
and pushes file to cloud used by Onyx. Later (or if run without arguments) it
lists some files from your send2boox account.


# Contribute

If you want to contribute then feel free to fork, edit and send back pull
requests, open issues etc.


# To do

- add error checking
- handle more API calls
