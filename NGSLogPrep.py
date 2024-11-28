import ctypes.wintypes
import logging
import os
from datetime import datetime, timezone
from glob import glob
from threading import Thread
from time import sleep


class NGSLogPrep:
    """Provides lines from PSO2(NGS) log files for easy further processing.

    Provides lines from the most recent PSO2(NGS) log file of a type or from a log file of specified path, while
    taking care of various hassles in this log files such as: cut off lines when receiving to many drops at once,
    line feed characters send in text messages, Byte Order Markers and many more.
    """

    def __init__(self, target: str, is_path=False) -> None:
        """Initializes an object representing a PSO2 log file

        If an object is instantiated using a log type instead of path, an additional thread is started to look for
        file path changes during execution. This for example happens, when the player hops between Base Game and NGS
        (different log file locations) or when a new log file is started at UTC Midnight.

        :param target: Either the type of most recent log file requested, or path to a specific log file
        :type target: str
        :param is_path: Overload parameter. Determines whenever target is interpreted as log type or path
            (default is False)
        :type is_path: bool

        :raises ValueError: If there is no PSO2 folder inside the Documents folder.
        :raises FileNotFoundError: If an absolute file path was provided, but no file was found.
        """

        # Store is_path to determine during file open if the Object is used for real time log parsing or one time
        self.__is_path = is_path
        # Define ByteArray set during file open later
        self.__ba = bytearray()

        # Log Type is provided for realtime read
        if not is_path:
            # Locate Documents Folder
            buf = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
            ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf)

            # Determine log folder across multiple PSO2 installations and Base Game/NGS in which was modified last
            # (Assume that this is the path someone wants to parse logs for)
            try:
                self.__log_path = os.path.dirname(max(glob(os.path.join(buf.value,
                                                                        r'SEGA\PHANTASYSTARONLINE2*\log*\*')),
                                                      key=os.path.getmtime))
            except ValueError as e:
                e_msg = rf'Found no suitable logs folder in "{buf.value}\SEGA\PHANTASYSTARONLINE2*\".'
                raise ValueError(e_msg) from e

            # Store log type for later use
            self.__log_type = target
            # PSO2 log files update at UTC Midnight
            self.__log_date = datetime.now(timezone.utc).strftime("%Y%m%d")
            # Assemble path to the newest log file of that type
            target = rf'{self.__log_path}\{self.__log_type}{self.__log_date}_00.txt'

            self.__open_log_file(target)

            # Update __log_path to parent directory for easier use in daemon later
            self.__log_path = os.path.dirname(self.__log_path)
            # Start daemon watching over log file path changes
            t = Thread(target=self.__log_monitor, daemon=True)
            t.start()

        # Absolute File Path is provided for one-time read
        else:
            if os.path.exists(target):
                self.__open_log_file(target)
            else:
                e_msg = f'No such log file: {target}'
                raise FileNotFoundError(e_msg)

    def __open_log_file(self, path: str) -> None:
        """ Opens a new log file for the object of type NGSLogPrep.

        The function will continue to attempt opening the log file, until it was successful. This may soft-locks the
        program if the log file existence is not checked before call, but can also be used intentionally wait for a
        log file to appear.

        :param path: The path to a file that is attempted to be opened.
        :type path: str
        """
        # Boolean to check whenever the file open previously failed
        error_fnf = False

        while True:
            try:
                # Attempt to open Logfile until success
                self.__f = open(path, 'rb')
            except FileNotFoundError:
                if not error_fnf:
                    error_fnf = True
                    logging.error(f'{self.__log_type} File not found! This should solve automatically once you started '
                                  f'the game or after sending/receiving a chat message or doing an action after UTC '
                                  f'Midnight. If the error persists, check if the specified log type is correct.')
            else:
                if not self.__is_path:
                    # File is read in realtime; skip to end of file
                    self.__f.seek(0, 2)
                else:
                    # File is read from the beginning once; skip over Byte Order Marker `\xff\xfe` (UTF-16-BE-BOM)
                    self.__f.read(2)

                # (Re)Set ByteArray to contain NULL `\x00` to make up for jumping to end of the file or skipping BOM
                # Not doing this will cause a UnicodeDecodeError
                # if-Case: Cursor is already in a new line with NULL skipped
                # else-Case: Cursor skipped over BOM, but next byte is invalid without NULL in front of it decoded first
                # This also doubles as cleaning up the bytearray if there is junk left from a previous log file
                self.__ba = bytearray(b'\x00')

                break

            sleep(1)

    def __log_monitor(self) -> None:
        """Function running as daemon to check if the log file path needs to be updated.

        Running as thread when an object of type NGSLogPrep without absolute path was created, this function checks if
        the player is moving between Base Game and NGS, or when UTC Midnight requires to read from a new log file.
        """

        base_path = os.path.join(self.__log_path, rf'log\{self.__log_type}{self.__log_date}_00.txt')
        ngs_path = os.path.join(self.__log_path, rf'log_ngs\{self.__log_type}{self.__log_date}_00.txt')
        while True:
            # Check if Player moved between Base Game and NGS
            if os.path.exists(base_path) and os.path.exists(ngs_path):
                last_path = max(base_path, ngs_path, key=os.path.getmtime)
                if last_path != self.__f.name:
                    self.__open_log_file(last_path)

            # Check for UTC Midnight to update Logfile Path
            current_date = datetime.now(timezone.utc).strftime('%Y%m%d')
            if current_date > self.__log_date:
                # Update Date, opened File and Base Game / NGS Log File paths
                self.__log_date = current_date
                self.__open_log_file(os.path.join(os.path.dirname(self.__f.name),
                                                  f'{self.__log_type}{self.__log_date}_00.txt'))
                base_path = os.path.join(self.__log_path, rf'log\{self.__log_type}{self.__log_date}_00.txt')
                ngs_path = os.path.join(self.__log_path, rf'log_ngs\{self.__log_type}{self.__log_date}_00.txt')

            sleep(15)

    def get_lines(self) -> list:
        """Gets new log lines since the last call of this function.

        When using the NGSLogPrep Object to receive log lines in realtime, you may want to poll this function.
        When using on a log file with absolute file path, the function only needs to be called once.

        :returns: A list of log lines
        :rtype: list
        """

        log_lines = list()

        # TODO Find a more performant way of reading the file
        # When going to fast, log lines may be cut off. As such, just reading everything, splitting through \r\n and
        # assuming the last line is a valid log line is not a viable strategy
        while byte := self.__f.read(1):
            self.__ba += byte
            # People may send the line feed character in ingame chat; a real "log line"-end also has a carriage return
            if self.__ba[-4:] == b'\x00\r\x00\n':
                try:
                    log_line = self.__ba[:-4].decode('UTF-16-BE')
                except UnicodeDecodeError:
                    logging.error(f'Unable to decode Log Line. Bytes in question: {self.__ba}')
                else:
                    log_lines.append(log_line)
                finally:
                    self.__ba = bytearray()
        return log_lines

    @property
    def log_path(self) -> str:
        """Gets and returns contents of the __log_path attribute

        :returns: content of __log_path attribute
        :rtype: str
        """
        return self.__log_path


# Naive implementation to see if stuff works as expected
if __name__ == '__main__':
    log = NGSLogPrep(r'C:\Users\PureFallen\Desktop\message.txt', is_path=True)
    while True:
        sleep(1)

        for line in log.get_lines():
            print(line)
