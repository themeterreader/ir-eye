import serial
import time
import struct
import binascii
import paho.mqtt.publish as publish
import json

##########################################################################################
# END-USER CONFIGURATION
DEBUG=True
DEBUG_MQTT_MESSAGES=False
MQTT_BROKER = '192.168.66.6' # <---------------------------------------------------------- YOUR MQTT BROKER ADDRESS HERE
MQTT_PORT = 1883
MQTT_USER = 'powermeterreader'
MQTT_PASSWORD = 'password' # <------------------------------------------------------------ YOUR MQTT PASSWORD HERE
SERIAL_PORT = '/dev/serial/by-id/usb-FTDI_FT230X_Basic_UART_DQ00LLF3-if00-port0' # <------ YOUR OUTPUT FROM 
PASSWORD = 'c3XXXXXXXXXXXXXXXX6b' # <----------------------------------------------------- YOUR 20-CHARACTER PASSWORD RECEIVED FROM METER COMPANY HERE
METER_ID = 'serial_XXXXXX' # <------------------------------------------------------------ YOUR METER SERIAL HERE
METER_MANUFACTURER = 'nes'
METER_MODEL = 'NES83334'
METER_NAME = 'MainMeter'
RUN_INTERVAL_SECS=10 #not used when run from cron
###########################################################################################

# No need to touch anything below
# Serial parameters for C12.18 device
BAUD_RATE = 9600  # C12.18 standard baud rate
TIMEOUT = 0.5  # Timeout in seconds (adjustable)
DATA_BITS = 8  # 8 data bits, standard for C12.18
STOP_BITS = 1  # 1 stop bit, standard
PARITY = 'N'  # No parity, standard for C12.18

ACK_BYTE = 0x06
MAX_RETRIES = 3

userid=99
username="fixeduser"
padded_username=f"{username:<10}"
SUPPORT_URL='https://github.com/themeterreader/ir-eye'

serial_messages = []

if len(PASSWORD) != 20:
    exit('PASSWORD must be exactly 20 characters\n "{PASSWORD}" is only {len(PASSWORD)}')

# Open the serial port
ser = serial.Serial(
    port=SERIAL_PORT,
    baudrate=BAUD_RATE,
    timeout=TIMEOUT,
    bytesize=DATA_BITS,
    stopbits=STOP_BITS,
    parity=PARITY
)

# MQTT parameters
mqtt_discovery_types={}
mqtt_discovery_types['energy_fwd'] = { 'deci': 3, 'dclass': 'energy',      'icon': 'mdi:transmission-tower-import', 'unit': 'kWh', 'sclass': 'total_increasing'}
mqtt_discovery_types['energy_rev'] = { 'deci': 3, 'dclass': 'energy',      'icon': 'mdi:transmission-tower-export', 'unit': 'kWh', 'sclass': 'total_increasing'}
mqtt_discovery_types['power_fwd'] =  { 'deci': 0, 'dclass': 'power', 'icon': 'mdi:import',                    'unit': 'W',   'sclass': 'measurement'}
mqtt_discovery_types['power_rev'] =  { 'deci': 0, 'dclass': 'power', 'icon': 'mdi:export',                    'unit': 'W',   'sclass': 'measurement'}
for i in range(1, 4):
    mqtt_discovery_types[f'current_L{i}'] = { 'deci': 2, 'dclass': 'current', 'icon': 'mdi:current-ac', 'unit': 'A',   'sclass': 'measurement'}
    mqtt_discovery_types[f'voltage_L{i}'] = { 'deci': 2, 'dclass': 'voltage', 'icon': 'mdi:sine-wave',  'unit': 'V',   'sclass': 'measurement'}



class PacketAndTransmissionhandler:
    # Takeaway from reading kupdf.net_ansi-c1218.pdf
    # "An example of a typical communications session would consist of the following services with
    #  appropriate responses, in the order listed: Identification, [Negotiate], Logon, Security, Table-Reads (or Writes), Logoff and Terminate."

# requests are sent in packets: <stp><identity><ctrl>                 <seq-nbr>      <length(word16)>   <data>    <crc(word16)>
    #                                0xEE   0x00   bit7=multipacket           0x00       data-bytes-count   x bytes   CCITT-poly
    #                                              bit6=first-of-multi        (not multi)
    #                                              bit5=toggle-bit <--- used
    #                                              bit0=C12.18-dataformat

    #  python-client  <---->   power-meter
    #    --->  <ident> = 0x20
    #    <---  <ident-response> = <ok><std><ver><rev><feature>*<end-of-list>
    #   (---->) <negotiate>
    #   (<----) <negotiate-response>
    #    --->  <logon> = 0x50 <user-id(word16)><user(10 bytes)>
    #    <---  <logon-response>
    #    --->  <security> = 0x51 <password(20bytes)>
    #    <---  <security-response>
    #    --->  <read-partial-offset-octet> = 0x3F <tableid(word16)><offset(word24)><octet-count(word16)>
    #    <---  <read-response> = <ok><count(word16)><data(byte)><cksum(1 byte, 2's complement)>  (or <nok>)
    #    --->  <logoff> = 0x52
    #    <---  <logoff-response>
    #    --->  <terminate> = 0x21
    #    <---  <terminate-response>

    # Send request, wait up to 2 secs - if no response, retransmit max 3 times
    # Stilhed i 6 sekunder 'nulstiller forbindelsen'

    def __init__(self):
        self.toggleBit = False

    """Send the bytes as a C12.18 packet and return the unpacked response. Handles 3 transmission attemps before failing."""
    def send(self, command_payload_string_of_hex_values, msgType):
        global serial_messages
        # make sure buffer if empty
        # calculate crc and append packaging
        ctrlbyte = "00"
        if self.toggleBit:
            ctrlbyte = "20"

        self.toggleBit = not self.toggleBit
        preamble = "EE00" + ctrlbyte + "00"
        payload_length = int(len(command_payload_string_of_hex_values)/2)
        payload_length_str = f'{payload_length:0>4X}'
        checksum_basis = preamble + payload_length_str + command_payload_string_of_hex_values
        crc = self.calcCRC(checksum_basis)
        fullC1218Packet = checksum_basis + crc
        serial_messages += " --> sending '" + fullC1218Packet + "'",
        attempt = 0
        while attempt < MAX_RETRIES:
            try:
                # Send the command to the meter
                attempt_info = ""
                if attempt != 0:
                    attempt_info =  f" (try#{attempt+1})"
                if DEBUG: 
                    print(f"Sending message: {msgType}{attempt_info}")
                    print(serial_messages[-1])
                ser.write(binascii.unhexlify(fullC1218Packet))

                # Read response from the meter
                response = ser.read(8192)

                if response:
                    serial_messages += f" <-- received '{response.hex()}'",
                    if DEBUG: print(serial_messages[-1])
                    last_serial_error = None
                    if response[0] != ACK_BYTE:
                        status.add_error(f"Response was not acknowledge for '{msgType}'")
                    return self.extractPayload(response[1:])
                else:
                    timeoutMsg = f"No response received, retrying... (Attempt {attempt+1}/{MAX_RETRIES} - {msgType})"
                    print(timeoutMsg)
                    status.add_error(timeoutMsg)
            except serial.SerialException as e:
                status.add_error(f"Error with serial communication: {e}")
            attempt += 1
            if DEBUG: print(" Sleep for 1 second before retrying")
            time.sleep(1)

        status.add_error("Message failed after max retries: " + msgType)
        return None

    def calcCRC(self, string_of_hex_values):
        hex_values = binascii.unhexlify(string_of_hex_values)
        bit_mask = 0xffff
        poly_mask = 0x8408 #0x1021 msb->lsb
        in_bit_mask = 0x8000
        out_bit_mask = 1
        value = 0xffff

        for byte in hex_values:
            for n in range(0, 8):
                out_bit = (value & out_bit_mask) != 0
                value = (value >> 1) & bit_mask
                if out_bit ^ bool((byte >> n) & 1):
                    value ^= poly_mask
        return binascii.hexlify(struct.pack('<H', value ^ 0xffff)).decode('utf-8')

    def extractPayload(self, all_bytes_in_packet):
        # 1 Verify CRC
        # 2 Strip preamble and return payload contents
        payload = all_bytes_in_packet[:-2]
        crc_received = all_bytes_in_packet[-2:]
        crc_from_payload = self.calcCRC(payload.hex())
        if crc_from_payload != crc_received.hex():
            raise serial.SerialException(f"CRC received did not match CRC calculated from received payload ({crc_from_payload} vs {crc_received})")
        data_length, = struct.unpack(">H", payload[4:6])
        payload = payload[6:]
        if data_length != len(payload):
            raise serial.SerialException(f"Length received did not match length of received payload ({data_length} vs {len(payload)})")
        return payload

    def send_ident(self):
        self.send('20', 'ident')

    def send_logon(self):
        logon_response = self.send(f"50{userid:0>4X}{binascii.a2b_qp(padded_username).hex()}", 'logon')
        if logon_response is None or logon_response[0] != 0:
            status.add_error(f"LOGON command failed. ({logon_response})")

    def send_security(self):
        security_response = self.send(f"51{binascii.a2b_qp(PASSWORD).hex()}", 'security')
        if security_response is None or security_response[0] != 0:
            status.add_error(f"SECURITY command failed. ({security_response})")

    def read_table_data(self, table_no, octet_count, offset = 0):
        read_result = self.send(f"3f{table_no:0>4X}{offset:0>6X}{octet_count:0>4X}", f'readtable{table_no}')
        if read_result[0] != 0:
            raise serial.SerialException(f"Non-OK response from table-read ({read_result[0]})")
        if octet_count != struct.unpack(">H", read_result[1:3])[0] :
            raise serial.SerialException(f"Number of octets read does not match the number reuested ({struct.unpack('>H', read_result[1:3])[0]} vs {octet_count})")
        read_result_checksum = read_result[-1]
        read_result = read_result[3:-1]
        calculated_checksum = ((sum(read_result) - 1) & 0xff) ^ 0xff
        if read_result_checksum != calculated_checksum:
            raise serial.SerialException(f"Calculated table data checksum does not match received checksum ({calculated_checksum} vs {read_result_checksum})")
        return read_result

    def calcCRC(self, string_of_hex_values):
        hex_values = binascii.unhexlify(string_of_hex_values)
        bit_mask = 0xffff
        poly_mask = 0x8408 #0x1021 msb->lsb
        in_bit_mask = 0x8000
        out_bit_mask = 1
        value = 0xffff

        for byte in hex_values:
            for n in range(0, 8):
                out_bit = (value & out_bit_mask) != 0
                value = (value >> 1) & bit_mask
                if out_bit ^ bool((byte >> n) & 1):
                    value ^= poly_mask
        return binascii.hexlify(struct.pack('<H', value ^ 0xffff)).decode('utf-8')

    def extractPayload(self, all_bytes_in_packet):
        # 1 Verify CRC
        # 2 Strip preamble and return payload contents
        payload = all_bytes_in_packet[:-2]
        crc_received = all_bytes_in_packet[-2:]
        crc_from_payload = self.calcCRC(payload.hex())
        if crc_from_payload != crc_received.hex():
            raise serial.SerialException(f"CRC received did not match CRC calculated from received payload ({crc_from_payload} vs {crc_received})")
        data_length, = struct.unpack(">H", payload[4:6])
        payload = payload[6:]
        if data_length != len(payload):
            raise serial.SerialException(f"Length received did not match length of received payload ({data_length} vs {len(payload)})")
        return payload

    def send_ident(self):
        self.send('20', 'ident')

    def send_logon(self):
        logon_response = self.send(f"50{userid:0>4X}{binascii.a2b_qp(padded_username).hex()}", 'logon')
        if logon_response is None or logon_response[0] != 0:
            status.add_error(f"LOGON command failed. ({logon_response})")

    def send_security(self):
        security_response = self.send(f"51{binascii.a2b_qp(PASSWORD).hex()}", 'security')
        if security_response is None or security_response[0] != 0:
            status.add_error(f"SECURITY command failed. ({security_response})")

    def read_table_data(self, table_no, octet_count, offset = 0):
        read_result = self.send(f"3f{table_no:0>4X}{offset:0>6X}{octet_count:0>4X}", f'readtable{table_no}')
        if read_result[0] != 0:
            raise serial.SerialException(f"Non-OK response from table-read ({read_result[0]})")
        if octet_count != struct.unpack(">H", read_result[1:3])[0] :
            raise serial.SerialException(f"Number of octets read does not match the number reuested ({struct.unpack('>H', read_result[1:3])[0]} vs {octet_count})")
        read_result_checksum = read_result[-1]
        read_result = read_result[3:-1]
        calculated_checksum = ((sum(read_result) - 1) & 0xff) ^ 0xff
        if read_result_checksum != calculated_checksum:
            raise serial.SerialException(f"Calculated table data checksum does not match received checksum ({calculated_checksum} vs {read_result_checksum})")
        return read_result

    def fetch_total_energy(self):
        table23_response = self.read_table_data(23, 8)
        if table23_response is None or len(table23_response) != 8:
            status.add_error(f"READTABLE-23 command failed. ({table23_response})")
            return
        status.energy_fwd = struct.unpack("<I", table23_response[0:4])[0] * 0.001
        status.energy_rev = struct.unpack("<I", table23_response[4:8])[0] * 0.001

    def fetch_immediate_values(self):
        table28_response = self.read_table_data(28, 40)
        if table28_response is None or len(table28_response) != 40:
            status.add_error(f"READTABLE-28 command failed. ({tabl28_response})")
            return
        status.power_fwd = struct.unpack("<I", table28_response[0:4])[0]
        status.power_rev = struct.unpack("<I", table28_response[4:8])[0]
        status.current_L1 = struct.unpack("<I", table28_response[16:20])[0] * 0.001
        status.current_L2 = struct.unpack("<I", table28_response[20:24])[0] * 0.001
        status.current_L3 = struct.unpack("<I", table28_response[24:28])[0] * 0.001
        status.voltage_L1 = struct.unpack("<I", table28_response[28:32])[0] * 0.001
        status.voltage_L2 = struct.unpack("<I", table28_response[32:36])[0] * 0.001
        status.voltage_L3 = struct.unpack("<I", table28_response[36:40])[0] * 0.001


    def send_logoff(self):
        logoff_response = self.send("52", 'logoff')
        if logoff_response is None or logoff_response[0] != 0:
            status.add_error(f"LOGOFF command failed. ({logoff_response})")

    def send_terminate(self):
        terminate_response = pkthandler.send("21", 'terminate')
        if terminate_response is None or terminate_response[0] != 0:
            status.add_error(f"TERMINATE command failed. ({terminate_response})")



class MeterReaderStatus:
    def __init__(self):
        self.reset()

    def reset(self):
        self.errors = []
        self.energy_fwd = -1
        self.energy_rev = -1
        self.power_fwd = -1
        self.power_rev = -1
        self.current_L1 = -1
        self.current_L2 = -1
        self.current_L3 = -1
        self.voltage_L1 = -1
        self.voltage_L2 = -1
        self.voltage_L3 = -1

    def add_error(self, error_message):
        self.errors.append(error_message)

    def has_errors(self):
        return len(self.errors) != 0

    def __repr__(self):
        return  '{\n' + '\n  '.join((str(item) + ' = ' + str(self.__dict__[item]) for item in sorted(set(self.__dict__)-{'errors'}))) + (('\n\n errors:' + '\n  '.join(str(errr) for errr in self.errors)) if len(self.errors) != 0 else '')+ '\n}'

def fetch_from_meter():
    # Consume anything already buffered
    ser.read(8192)

    # 1) send ident, expect OK (and ignore rest for now)
    # 2) send logon, expect OK
    # 3) send security expect OK
    # 4) send read-partial command for 8 bytes of table 23, expect OK and remember values
    # 5) send read-partial command for 40 bytes of table 28, expect OK and remember values
    # 6) send logoff, expect OK
    # 7) send terminate, expect OK
    # 6) push values read - or in case of unexpected answers, an errordescription - to MQTT

    pkthandler.send_ident()
    if status.has_errors():
        return

    pkthandler.send_logon()
    if status.has_errors():
        return

    pkthandler.send_security()
    if status.has_errors():
        return

    pkthandler.fetch_total_energy()
    if status.has_errors():
        return

    pkthandler.fetch_immediate_values()
    if status.has_errors():
        return

    pkthandler.send_logoff()
    if status.has_errors():
        return

    pkthandler.send_terminate()


def publish_to_MQTT():
    msgs = ()

    device_config = {'identifiers': METER_ID, 'name': METER_NAME, 'manufacturer': METER_MANUFACTURER, 'serial_number': METER_ID, 'model': METER_MODEL}
    for sensor in mqtt_discovery_types.keys():
        unique_id = f'{METER_NAME}_{sensor}'
        state_topic = f'meterreader/sensor/{unique_id}'
        sensor_config = {
            'name': unique_id,
            'device': device_config,
            'unique_id': unique_id,
            'device_class': mqtt_discovery_types[sensor]['dclass'],
            'unit_of_measurement': mqtt_discovery_types[sensor]['unit'],
            'icon': mqtt_discovery_types[sensor]['icon'],
            'state_class': mqtt_discovery_types[sensor]['sclass'],
            'state_topic': state_topic
        }
        msgs += { 'topic': f'homeassistant/sensor/{unique_id}/config' , 'payload' : json.dumps(sensor_config)},

        reading_value=getattr(status, sensor)
        if reading_value == -1:
            continue
        msgs += { 'topic' : state_topic, 'payload' : f"{round(reading_value, mqtt_discovery_types[sensor]['deci'])}"},

    if DEBUG_MQTT_MESSAGES:
        for m in msgs:
            print(json.dumps(m))

    publish.multiple(msgs, hostname = MQTT_BROKER, port = MQTT_PORT, auth = {'username': MQTT_USER, 'password': MQTT_PASSWORD})




pkthandler = PacketAndTransmissionhandler()
status = MeterReaderStatus()

def fetch_and_publish_once():
    status.reset()

    fetch_from_meter()
    if not status.has_errors():
        publish_to_MQTT()
        if DEBUG and not status.has_errors():
            print(time.strftime("%F %T"))
            print(status)
    if status.has_errors():
        print("Errors:")
        for e in status.errors: print(" " + e)
        print("Communication:")
        for com in serial_messages: print(" " + com)

def fetch_and_publish_continuously():
    # run on the interval
    initial_sleep = RUN_INTERVAL_SECS - time.time() % RUN_INTERVAL_SECS
    if DEBUG: print(f"Sleep for {initial_sleep:0.0f} second(s) before kicking off")
    time.sleep(initial_sleep)

    while(True):
        loop_start_time = time.time()
        fetch_and_publish_once()
        loop_sleep = RUN_INTERVAL_SECS-time.time()+loop_start_time
        if DEBUG: print(f"Sleep for {loop_sleep:0.0f} second(s) before requesting again")
        time.sleep(loop_sleep)

if __name__ == "__main__":
    #fetch_and_publish_continuously()
    fetch_and_publish_once() #use cron to spark the action

