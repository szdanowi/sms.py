#!/usr/bin/env python3

import fcntl
import os
import re
import sys
import termios
import tty
from concurrent.futures.thread import ThreadPoolExecutor
from time import sleep

from colorama import Fore, Style


def g_note(what: str):
    print("\033[s\033[K{}\033[u".format(what), end='', flush=True)


def g_show(what: str, style='', end='\n'):
    print("\033[K{}{}".format(style, what) + Style.RESET_ALL, end=end, flush=True)


def g_silent(*args, **kwargs):
    pass


def g_nc_show(what: str, style='', end='\n'):
    print(what, end=end, flush=True)


class Print:
    note = g_note
    show = g_show

    @staticmethod
    def disable_colors():
        Print.note = g_silent
        Print.show = g_nc_show

    @staticmethod
    def outgoing(what):
        Print.note("< " + what)

    @staticmethod
    def incoming(what):
        Print.note("> " + what)

    @staticmethod
    def error(what):
        Print.show(what, Fore.RED)

    @staticmethod
    def step(what):
        Print.show(what + " ... ", Fore.MAGENTA, end='')

    @staticmethod
    def ok() -> bool:
        Print.show("Ok", Fore.GREEN)
        return True

    @staticmethod
    def fixed() -> bool:
        Print.show("Fixed", Fore.YELLOW)
        return True

    @staticmethod
    def fail() -> bool:
        Print.show("Failed", Fore.GREEN)
        return False

    @staticmethod
    def result(result):
        if result:
            Print.ok()
        else:
            Print.fail()
        return result

    @staticmethod
    def debug(what):
        Print.show(what)


class Animation:
    FRAMES = "\\|/-"

    def __init__(self, silent_for: int = 0):
        self.frame = -silent_for

    def show(self):
        if self.frame < 0:
            self.frame += 1
            return

        self.frame = (self.frame + 1) % len(self.FRAMES)
        Print.note("{}".format(self.FRAMES[self.frame]))


class AtModem:
    TIMEOUT_DS = 5  # deciseconds

    def __init__(self, device: str):
        self.__device = device  # should be e.g. /dev/ttyUSB0
        self.log = []

    def works(self) -> bool:
        Print.step('Checking modem')

        if self.__at():
            return Print.ok()

        return Print.fixed() if self.abort() and self.__at() else Print.fail()

    def make_command(self, what: str, timeout: int = 5) -> (None, str):
        with ThreadPoolExecutor(max_workers=1) as executor:
            future_response = executor.submit(self.listen, timeout)
            sleep(1)
            self.send(what)
            return future_response.result()

    def send(self, what):
        Print.outgoing(what)
        self.log.append("> " + what)
        with open(self.__device, 'wb') as file:
            file.write('{}\r\n'.format(what).encode())

    def listen(self, seconds: int = 5) -> (None, str):
        animation = Animation(silent_for=2)
        remaining = seconds * 10 / self.TIMEOUT_DS

        with open(self.__device, 'rb') as output:
            result = None
            old_flags, old_ios = self.__configure(output.fileno())

            try:
                while remaining > 0 and not result:
                    line = output.readline()

                    if line and len(line.decode().strip()) > 0:
                        result = line.decode().strip()
                        self.log.append("< " + result)
                        Print.incoming(result)
                    else:
                        animation.show()
                        remaining -= 1
            finally:
                self.__deconfigure(output.fileno(), old_flags, old_ios)

            return result

    @staticmethod
    def __deconfigure(fd, old_flags, old_ios):
        fcntl.fcntl(fd, fcntl.F_SETFL, old_flags)
        termios.tcsetattr(fd, termios.TCSAFLUSH, old_ios)

    @staticmethod
    def __configure(fd):
        old_flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, old_flags & ~os.O_NONBLOCK)

        old_ios = termios.tcgetattr(fd)
        new_ios = termios.tcgetattr(fd)
        new_ios[tty.LFLAG] = new_ios[tty.LFLAG] & ~termios.ICANON & ~termios.ECHO
        new_ios[tty.CC][tty.VMIN] = 0
        new_ios[tty.CC][tty.VTIME] = AtModem.TIMEOUT_DS
        termios.tcsetattr(fd, termios.TCSAFLUSH, new_ios)

        return old_flags, old_ios

    def __at(self) -> bool:
        return self.make_command('AT') == "OK"

    def abort(self) -> bool:
        return self.make_command('\033') in ("OK", "+CMS ERROR: 305")

    def switch_to_gsm(self) -> bool:
        Print.step('Switching modem to GSM mode')
        return Print.result(self.make_command('AT+CSCS="GSM"') == "OK")

    def switch_to_text_mode(self) -> bool:
        Print.step('Selecting text mode')
        return Print.result(self.make_command('AT+CMGF=1') == "OK")

    def select_receiver(self, number: str) -> bool:
        Print.step('Setting SMS receiver')
        return Print.result(self.make_command('AT+CMGS="{}"'.format(number)) == ">")

    SMS_SEND_RESPONSE_RE = re.compile(r'[+]CMGS:\s*\d*')

    def send_message(self, content: str) -> bool:
        Print.step('Entering message content')

        lines = content.splitlines(keepends=False)
        for line in lines[:-1]:
            if self.make_command(line) != ">":
                return Print.fail()

        return Print.result(self.SMS_SEND_RESPONSE_RE.match(self.make_command('{}\032'.format(lines[-1]))) is not None)


class TerminateApplication(RuntimeError):
    def __init__(self, message):
        super().__init__(self)
        self.what = message


def assure(what: bool, failure_message: str):
    if not what:
        raise TerminateApplication("Fatal: " + failure_message)


def print_help(exec_name):
    print("Command line tool for sending SMSes over an AT-compatible USB GSM modem",
          "",
          "Usage: {} [options] [PHONE_NUMBER] [MESSAGE]".format(exec_name),
          "",
          "  PHONE_NUMBER : needs to be a polish phone number, in one of two formats:",
          "                 +48123456789 or 123456789",
          "  MESSAGE      : contents of the SMS to be sent - does not need to be quoted",
          "",
          "Available options:",
          "  -d DEVICE    : tells tool to use DEVICE instead of /dev/ttyUSB0 as the modem",
          "  --no-color   : tells tool not to use colored output"
          "  --help       : shows this help message and exits",
          "",
          "Example uses:",
          "  {} 123456789 Will you call me?".format(exec_name),
          "  {} -d /dev/ttyUSB2 987654321 \"Hello there!\"".format(exec_name),
          sep='\n')


PHONE_PATTERN = re.compile(r'([+]48)?\s?(?P<number>\d{9})')


def main(exec_name, arguments: list):
    if '--no-color' in arguments:
        Print.disable_colors()
        arguments.remove('--no-color')

    if '--help' in arguments or '-h' in arguments:
        print_help(exec_name)
        exit(0)

    modem_device = '/dev/ttyUSB0'
    if '-d' in arguments:
        try:
            index = arguments.index('-d')
            arguments.pop(index)
            modem_device = arguments.pop(index)
        except Exception as e:
            Print.error("\nCould not find device to be set as the modem")
            exit(2)

    modem = AtModem(modem_device)

    try:
        print()
        assure(len(arguments) >= 2, "Two arguments were expected: phone number and message")

        matched_phone_number = PHONE_PATTERN.match(arguments[0])
        assure(matched_phone_number is not None,
               "Expected phone number to match polish format: +48123456789 or 123456789")
        phone_number = "+48" + matched_phone_number.group('number')

        message_contents = ' '.join(arguments[1:]).strip()
        assure(len(message_contents) <= 160,
               "Your messsage is too long - it has {} characters when the limit is 160".format(len(message_contents)))

        Print.show("--- telling ", Fore.MAGENTA, end='')
        Print.show(phone_number, Fore.GREEN, end='')
        Print.show(" --------", Fore.MAGENTA)
        Print.show(message_contents, Fore.GREEN)
        Print.show("---------------------------------\n", Fore.MAGENTA)

        assure(modem.works(), "Your modem does not seem to be working")
        assure(modem.switch_to_gsm(), "Could not switch modem to GSM mode")
        assure(modem.switch_to_text_mode(), "Could not switch modem to Text mode")
        assure(modem.select_receiver(phone_number), "Could not set message receiver number")
        assure(modem.send_message(message_contents), "Could not sent message")

        Print.show("\n                    Message sent.", Fore.GREEN)
        Print.show("---------------------------------\n", Fore.MAGENTA)

    except TerminateApplication as e:
        Print.error("\nFatal: " + e.what)
        if len(modem.log) > 0:
            Print.debug("\nHere's what the modem said:")
            for e in modem.log:
                Print.debug('  ' + e)
        exit(1)

    except Exception as e:
        Print.error("\nFatal: " + str(e))
        exit(2)


if __name__ == '__main__':
    main(sys.argv[0], sys.argv[1:])

print(Style.RESET_ALL)
