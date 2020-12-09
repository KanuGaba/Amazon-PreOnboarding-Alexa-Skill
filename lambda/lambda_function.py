# -*- coding: utf-8 -*-

# This sample demonstrates handling intents from an Alexa skill using the Alexa Skills Kit SDK for Python.
# Please visit https://alexa.design/cookbook for additional examples on implementing slots, dialog management,
# session persistence, api calls, and more.
# This sample is built using the handler classes approach in skill builder.
import logging
import json
import boto3
from boto3.dynamodb.conditions import Key

import os

import requests
import calendar
import random
from datetime import datetime
from pytz import timezone
from typing import Dict, Any

from botocore.exceptions import ClientError

import ask_sdk_core.utils as ask_utils
from ask_sdk_core.utils import get_supported_interfaces
# from ask_sdk_core.skill_builder import SkillBuilder
from ask_sdk_core.skill_builder import CustomSkillBuilder
from ask_sdk_core.dispatch_components import AbstractRequestHandler
from ask_sdk_core.dispatch_components import AbstractExceptionHandler
from ask_sdk_core.handler_input import HandlerInput

from ask_sdk_core.api_client import DefaultApiClient

from ask_sdk.standard import StandardSkillBuilder

from ask_sdk_model.directive import Directive
from ask_sdk_model.dialog.delegate_directive import DelegateDirective
from ask_sdk_model.ui.ask_for_permissions_consent_card import \
    AskForPermissionsConsentCard
from ask_sdk_model.services.service_client_factory import ServiceClientFactory
from ask_sdk_model.ui import SimpleCard
from ask_sdk_model.intent_confirmation_status import IntentConfirmationStatus

from ask_sdk_model import Response
from ask_sdk_model.interfaces.alexa.presentation.apl import (
    RenderDocumentDirective)

# How to play with S3? https://github.com/alexa/skill-sample-python-first-skill/tree/master/module-3
from ask_sdk_s3.adapter import S3Adapter
s3_adapter = S3Adapter(bucket_name=os.environ["S3_PERSISTENCE_BUCKET"])

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


#################################################################
####################### Helper Functions ########################
#################################################################

def create_dynamo_boto_object():
    creds = {}
    with open('aws_creds.json', 'r') as json_file:
        creds = json.load(json_file)

    return boto3.resource('dynamodb',
                          aws_access_key_id=creds['aws_access_key_id'],
                          aws_secret_access_key=creds['aws_secret_access_key'])

db = create_dynamo_boto_object()
intern_table = db.Table('intern_hackathon_users_table')
buckets_table = db.Table('intern_hackathon_buckets_table')

def put_user_row(item):
    return intern_table.put_item(Item=item)

slot_vals = {
    "gender": ["male", "female", "non binary"],
    "cleanliness": ["neat", "average", "messy"],
    "active_hours": [ "early riser", "night person","night owl","night","morning person", "morning"],
    "personality": ['introverted', 'extroverted']
}

def get_items_from_file(file_name):
    items = []
    with open(file_name, "r") as f:
        line = f.readline().strip()
        while line:
            items.append(line)
            line = f.readline().strip()
    return items

def get_random_item(vals):
    return vals[ round( random.random() * ( len(vals) - 1 ) ) ]

def random_digit():
    return str(int(random.random() * 9))


def get_random_phone():
    num = ""
    for i in range(0, 10):
        num += random_digit()
    return num   

def get_email(name):
    name = ("").join(name.split(" "))
    id_num = ""
    for i in range(0, 6):
        id_num += str( int( random.random() * 9  ) )
    return name + id_num +  "@gmail.com"

def generate_compatible_roommates(user_item):
    male_names = get_items_from_file("male_names.txt")
    female_names = get_items_from_file("female_names.txt")
    absolute_match_traits = ["office", "room_type"]
    traits = ["gender", "cleanliness", "active_hours", "personality"]
    preferences = ["roommate_gender_preference", "cleanliness_preference", "active_hours_preference", "personality_preference"]

    matches = []
    for _ in range(0, 10):
        fake_roommate = {}
        
        # goes through each preference
        for i in range(0, 4):
            # flexible preference of user_item, can pick random trait for fake_user
            if user_item[preferences[i]] == "no preference":
                fake_roommate[traits[i]] = get_random_item(slot_vals[traits[i]])
                fake_roommate[preferences[i]] = user_item[traits[i]]
            else:
                # has discrete preference, must make sure preferences and traits match
                fake_roommate[traits[i]] = user_item[preferences[i]]
                gate = (random.random() * 2 ) > 1
                # randomly decide if to give no preference or discrete preference
                if gate:
                    fake_roommate[preferences[i]] = user_item[traits[i]]
                else:
                    fake_roommate[preferences[i]] = "no preference"

        if fake_roommate["gender"] == "male":
            fake_roommate["name"] = get_random_item(male_names)
        else:
            fake_roommate["name"] = get_random_item(female_names)
            
        fake_roommate["office"] = user_item["office"]
        fake_roommate["room_type"] = user_item["room_type"]
        fake_roommate["interesting_fact"] = get_random_item(get_items_from_file("facts.txt"))
        fake_roommate["random_phone"] = get_random_phone()
        fake_roommate["email"] = get_email(fake_roommate["name"] )
        matches.append(fake_roommate)

    logger.info(matches)
    return matches

def insert_into_compatability_bucket(user_item):
    response = buckets_table.query(
        KeyConditionExpression=Key('survey-vals').eq(user_item["bucket_str"])
    )
    logger.info(str(response))
    items = response["Items"]
    item = None
    if not items :
        item = {'survey-vals': user_item['bucket_str'], 'users': []}
    else:
        item = items[0]

    if not user_item['name-phone-email'] in item["users"]:
        item["users"].append(user_item['name-phone-email'])
    logger.info(item)
    return buckets_table.put_item(Item=item)

def get_bucket_str(resp):
    return (  resp["office"] + "/" + resp["gender"] + "<>" + resp["roommate_gender_preference"] +
                    "/" + resp["room_type"] + "/" + resp["cleanliness"] + "<>" +
                    resp["cleanliness_preference"] + "/" +  resp["active_hours"] + "<>" + resp["active_hours_preference"] +
                    "/" + resp["personality"] + "<>" + resp["personality_preference"] + "/"
    )


def get_user_data(handler_input):
    user_data = get_user_data_request(handler_input)
    return user_data

def stringify_user_data(ud):
    phone = {"phone_number": "No number"}
    if ud["phone_number"]:
        phone = ud["phone_number"].to_dict()
    return (ud['name'] + "<>" + ud["email"] + "<>" + phone["phone_number"])

def get_user_data_request(handler_input):
    logger.info("Getting user data...")
    try:
        api_token = handler_input.request_envelope.context.system.api_access_token
        client = handler_input.service_client_factory.get_ups_service()
        data = {}
        data['name'] = client.get_profile_name()
        data['email'] = client.get_profile_email()
        data['phone_number'] = client.get_profile_mobile_number()
        return data
    except:
        return {}

def validate_persistent_attributes(persistent_attributes):
    """
    Validates if persistent attributes were initialized correctly, raises
    RuntimeError otherwise.
    :param persistent_attributes:
    :return: None
    """
    if "first_time_day_zero_user" not in persistent_attributes:
        error_msg = "No 'first_time_day_zero_user' in attributes, " \
                    "persistent_attributes were not initialized correctly"
        logger.error(error_msg)
        raise RuntimeError(error_msg)

    if "start_day_attributes" not in persistent_attributes:
        error_msg = "No 'start_day_attributes' in attributes," \
                    "persistent_attributes were not initialized correctly"

        logger.error(error_msg)
        raise RuntimeError(error_msg)

    if "roommate_survey_attributes" not in persistent_attributes:
        error_msg = "No 'roommate_survey_attributes' in attributes," \
                    "persistent_attributes were not initialized correctly"

        logger.error(error_msg)
        raise RuntimeError(error_msg)

    return


def generate_user_reminders(diff_days, reminders_already_received):
    """
    :param diff_days: The difference in days between the users start date and
    current date
    :param reminders_already_received: the reminders the user has already
    received
    :return: generated reminder_string
    """
    logger.info(f"Running 'generate_user_reminders', with diff_days={diff_days}, reminders_already_received={str(reminders_already_received)}.")
    initial_reminder_count = len(reminders_already_received)
    
    if diff_days < 46:
        initial_string = f"Your internship starts in {diff_days} days. Please " \
                            f"reach out to offers on boarding if you have not " \
                            f"yet received an email about"
    else:
        initial_string = ""

    possible_reminders = {
        "45d_r1": "Your background check. ",
        "30d_r1": "Immigration processing, if it applies. ",
        "30d_r2": "Your managers contact information. ",
        "30d_r3": "Relocation information from Graebal. ",
        "14d_r1": "Your my docs portal email. ",
        "3d_r1": "Information about new hire orientation. "
    }

    reminders_to_say = []
    if diff_days < 46 and "45d_r1" not in reminders_already_received:
        reminders_to_say.append(possible_reminders["45d_r1"])
        reminders_already_received.append("45d_r1")
    if diff_days < 31 and "30d_r1" not in reminders_already_received:
        reminders_to_say.append(possible_reminders["30d_r1"])
        reminders_already_received.append("30d_r1")
    if diff_days < 31 and "30d_r2" not in reminders_already_received:
        reminders_to_say.append(possible_reminders["30d_r2"])
        reminders_already_received.append("30d_r2")
    if diff_days < 31 and "30d_r3" not in reminders_already_received:
        reminders_to_say.append(possible_reminders["30d_r3"])
        reminders_already_received.append("30d_r3")
    if diff_days < 15 and "14d_r1" not in reminders_already_received:
        reminders_to_say.append(possible_reminders["14d_r1"])
        reminders_already_received.append("14d_r1")
    if diff_days < 4 and "3d_r1" not in reminders_already_received:
        reminders_to_say.append(possible_reminders["3d_r1"])
        reminders_already_received.append("3d_r1")
        
    final_reminder_count = len(reminders_already_received)
    if final_reminder_count == initial_reminder_count:
        return "You have no new reminders. ", reminders_already_received
    
    if abs(final_reminder_count - initial_reminder_count) > 1:
        the_following = " the following. "
    else:
        the_following = ". "
    
    full_reminder_string = initial_string + the_following 
    
    num_new_reminders = len(reminders_to_say)
    if num_new_reminders == 1:
        full_reminder_string += reminders_to_say[0]
    elif num_new_reminders == 2:
        full_reminder_string += reminders_to_say[0] + reminders_to_say[1]
    elif num_new_reminders > 2:
        for i in range(num_new_reminders - 1):
            full_reminder_string += reminders_to_say[i]
        full_reminder_string += "And finally. "
        full_reminder_string += reminders_to_say[num_new_reminders - 1]

    return full_reminder_string, reminders_already_received

#################################################################
##################### Multimodal Functions ######################
#################################################################

BACKGROUND_URLS = [
    "https://images.fastcompany.net/image/upload/w_1280,f_auto,q_auto,fl_lossy/wp-cms/uploads/2019/04/p-2-peccy.jpg",
    "https://upload.wikimedia.org/wikipedia/commons/1/15/Amazon_Spheres_05.jpg",
    "https://www.ft.com/__origami/service/image/v2/images/raw/https:%2F%2Fs3-ap-northeast-1.amazonaws.com%2Fpsh-ex-ftnikkei-3937bb4%2Fimages%2F4%2F7%2F9%2F2%2F13942974-2-eng-GB%2F20180518_Jeff_Wilke.jpg?source=nar-cms",
    "https://miro.medium.com/max/600/0*zi1Ii_wJnLat347w.",
    "https://static01.nyt.com/images/2013/08/17/technology/17bits-bezos-1998/17bits-bezos-1998-tmagArticle.jpg"
]
# APL Document file paths for use in handlers
multimodal_template_path = "multimodal_template.json"
data_sources_path = "data_sources.json"

# Tokens used when sending the APL directives
FAQ_TOKEN = "FAQToken"

def add_apl(handler_input, response_builder, speak_output):
    # type: ()
    if get_supported_interfaces(handler_input).alexa_presentation_apl is not None:
        data_sources = _load_apl_document(data_sources_path)
        modify_document(data_sources, speak_output)

        response_builder.add_directive(
            RenderDocumentDirective(
                token=FAQ_TOKEN,
                document=_load_apl_document(multimodal_template_path),
                datasources=data_sources
            )
        )


def _load_apl_document(file_path):
    # type: (str) -> Dict[str, Any]
    """Load the apl json document at the path into a dict object."""
    with open(file_path) as f:
        return json.load(f)

def modify_document(document, text):
    # type: (json, str) -> void (modifies json doc to hold str)
    document["text"]["content"] = text
    document["imageUrl"]["content"] = random.choice(BACKGROUND_URLS)

#################################################################
######################## Launch Handler #########################
#################################################################


class LaunchRequestHandler(AbstractRequestHandler):
    """Handler for Skill Launch."""

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool

        return ask_utils.is_request_type("LaunchRequest")(handler_input)

    @staticmethod
    def get_user_reminders(handler_input):
        logger.info("calling 'get_user_reminders'")
        gracefull_fallback = "You have no new reminders. "

        persistent_attributes = handler_input.attributes_manager.persistent_attributes

        start_day_attributes = persistent_attributes["start_day_attributes"]

        try:
            year = int(start_day_attributes['year'])
        except ValueError:
            logger.error("There was a problem retrieving year from "
                         f"persistent_attributes, got '{start_day_attributes['year']}'. ")
            return gracefull_fallback

        month = start_day_attributes['month']
        # month is a string, and we need to convert it to a month index later
        
        try:
            day = int(start_day_attributes['day'])
        except ValueError:
            logger.error("There was a problem retrieving day from "
                         f"persistent_attributes, got '{start_day_attributes['day']}'. ")
            return gracefull_fallback

        # get device id
        sys_object = handler_input.request_envelope.context.system
        device_id = sys_object.device.device_id

        # get systems api information
        api_endpoint = sys_object.api_endpoint
        api_access_token = sys_object.api_access_token
        # construct systems api timezone url
        url = f"{api_endpoint}/v2/devices/{device_id}/settings/System.timeZone"
        headers = {'Authorization': 'Bearer ' + api_access_token}

        userTimeZone = ""
        try:
            r = requests.get(url, headers=headers)
            res = r.json()
            logger.info("Device API result: {}".format(str(res)))
            userTimeZone = res
        except Exception:
            logger.error("There was a problem connecting to the service. ")
            return gracefull_fallback
            # handler_input.response_builder.speak("There was a problem"
            #                                      " connecting to the service")
            # return handler_input.response_builder.response

        # getting the current date with the time
        now_time = datetime.now(timezone(userTimeZone))
        # Removing the time from the date because it affects our difference calculation
        now_date = datetime(now_time.year, now_time.month, now_time.day)

        # getting the next birthday
        start_month_as_index = list(calendar.month_abbr).index(
            month[:3].title())
        
        try:
            start_date = datetime(year, start_month_as_index, day)
        except ValueError:
            logger.error(f"Error caught in get_user_reminders, datetime threw exception. Had year={year}, start_month_as_index={start_month_as_index}, day={day}")
            return gracefull_fallback

        diff_days = abs((now_date - start_date).days)

        reminders_already_received = start_day_attributes[
            "reminders_already_received"]
        reminder_string, rar = generate_user_reminders(diff_days,
                                                       reminders_already_received)

        # update persistent attributes
        start_day_attributes["reminders_already_received"] = rar
        handler_input.attributes_manager.persistent_attributes[
            "start_day_attributes"] = start_day_attributes
        handler_input.attributes_manager.save_persistent_attributes()

        return reminder_string

    @staticmethod
    def initialize_persistent_attributes():
        logger.info("calling 'initialize_persistent_attributes' from LaunchRequestHandler ")

        """
        This initializes persistent_attributes for a given customer.
        Used to maintain persistence between different invocations of the skill.
        If you are adding a new attribute, add a key and initialize to None.
        (Make sure to update the validate_persistent_attributes function too)
        """
        return {
            "first_time_day_zero_user": False,
            "start_day_attributes": None,
            "roommate_survey_attributes": None
        }

    @staticmethod
    def get_dynamic_speaker_text(handler_input):
        """
        This function dynamically returns speaker text based on the users
        current 'achievements'
        :param persistent_attributes:
        :return: speaker_text
        """
        logger.info("calling 'get_dynamic_speaker_text'")
        # TODO: Rohan please check the roomate matching survey intent

        persistent_attributes = handler_input.attributes_manager.persistent_attributes

        # base case
        speaker_text = "Welcome to Day Zero, this is a one stop shop for all of " \
                       "your pre onboarding needs. You can start the roommate " \
                       "matching survey, subscribe to internship reminders, ask for an " \
                       "Amazon fact, or ask me a pre onboarding question. "

        # They have started the roommate survey and subscribed to reminders
        if (persistent_attributes[
                "roommate_survey_attributes"] is not None) and (
                persistent_attributes["start_day_attributes"] is not None):
            speaker_text = f"Welcome to Day Zero. " \
                           f"{LaunchRequestHandler.get_user_reminders(handler_input)}" \
                           f"Do you have pre onboarding questions? I can also tell you fun Amazon facts. "

        # They have started the roommate survey and NOT subscribed to reminders
        if (persistent_attributes[
                "roommate_survey_attributes"] is not None) and (
                persistent_attributes["start_day_attributes"] is None):
            speaker_text = "Welcome to Day Zero. You can subscribe to internship " \
                           "reminders, or ask me a question. I can also tell you Amazon facts. "

        # They have NOT started the roommate survey and subscribed to reminders
        if (persistent_attributes["roommate_survey_attributes"] is None) and (
                persistent_attributes["start_day_attributes"] is not None):
            speaker_text = f"Welcome to Day Zero. " \
                           f"{LaunchRequestHandler.get_user_reminders(handler_input)}" \
                           f"Would you like to ask me any questions or " \
                           f"start the roommate matching survey? You can also ask me for fun facts. "

        return speaker_text

    @staticmethod
    def get_dynamic_reprompt(persistent_attributes):
        """
        This function dynamically returns reprompt text based on the users
        current 'achievements'
        :param persistent_attributes:
        :return: Reprompt
        """
        logger.info("calling 'get_dynamic_reprompt' from LaunchRequestHandler")
        # TODO: Rohan please check the roomate matching survey intent

        # base case
        reprompt_text = "You can say things like, I want to subscribe to " \
                        "internship reminders. Or you can say, " \
                        "start roommate matching survey. Or you can ask me a" \
                        " question. Or you can ask me for Amazon facts. What would you like to do? "

        # They have started the roommate survey and subscribed to reminders
        if (persistent_attributes[
                "roommate_survey_attributes"] is not None) and (
                persistent_attributes["start_day_attributes"] is not None):
            reprompt_text = "What would you like to ask? "

        # They have started the roommate survey and NOT subscribed to reminders
        if (persistent_attributes[
                "roommate_survey_attributes"] is not None) and (
                persistent_attributes["start_day_attributes"] is None):
            reprompt_text = "You can say things like, I want to subscribe to " \
                            "internship reminders. Or you can ask me a" \
                            " question. Or you can ask me for Amazon facts. What would you like to do? "

        # They have NOT started the roommate survey and subscribed to reminders
        if (persistent_attributes["roommate_survey_attributes"] is None) and (
                persistent_attributes["start_day_attributes"] is not None):
            reprompt_text = "You can say things like, start roommate matching " \
                            "survey. Or you can ask me a question. What would " \
                            "you like to do? "

        return reprompt_text

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("calling 'handle' in LaunchRequestHandler")


        # extract persistent attributes
        user_attributes = handler_input.attributes_manager.persistent_attributes
        logger.info(f"in 'handle' in LaunchRequestHandler, user_attributes={str(user_attributes)}")

        # They are a 1st time day zero user
        if "first_time_day_zero_user" not in user_attributes:
            user_attributes = self.initialize_persistent_attributes()
            validate_persistent_attributes(user_attributes)

            handler_input.attributes_manager.persistent_attributes = user_attributes
            handler_input.attributes_manager.save_persistent_attributes()

        # They are not a first time user...
        validate_persistent_attributes(user_attributes)

        speak_output = self.get_dynamic_speaker_text(handler_input)
        reprompt_text = self.get_dynamic_reprompt(user_attributes)
        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)

        return (response_builder.speak(speak_output).ask(
            reprompt_text).response)


#################################################################
######################### FAQ Handlers ##########################
#################################################################

FAQ_REPROMPT_TEXTS = ["Do you have any other questions?", "Anything else you want to ask?", "Anymore questions?"]

class YesIntentHandler(AbstractRequestHandler):
    """Handler for Yes/Question Intent."""

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("AMAZON.YesIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        speak_output = "Go ahead and ask"
        reprompt_text = random.choice(FAQ_REPROMPT_TEXTS)
        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)

        return (response_builder.speak(speak_output).ask(
            reprompt_text).response)


class PreparingIntentHandler(AbstractRequestHandler):
    """Handler for Preparing Intent."""

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("PreparingIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        speak_output = "We encourage you to be familiar with the Leadership Principles, but there is no need to memorize them. " \
                       "All of what you will need to be successful in your role will be provided to you by your team and other internal resources. " \
                       "Just relax as much as you can and enjoy your time prior to starting!!"
        reprompt_text = random.choice(FAQ_REPROMPT_TEXTS)
        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)

        return (response_builder.speak(speak_output).ask(
            reprompt_text).response)


class EquipmentIntentHandler(AbstractRequestHandler):
    """Handler for Equipment Intent."""

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("EquipmentIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        speak_output = "Amazon will provide a laptop, monitor, mouse, keyboard, and headset to ensure you have what you need for your internship. " \
                       "If you require additional items, please work with your manager."
        reprompt_text = random.choice(FAQ_REPROMPT_TEXTS)
        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)

        return (response_builder.speak(speak_output).ask(
            reprompt_text).response)


class CountryOfOfferIntentHandler(AbstractRequestHandler):
    """Handler for Country of Offer Intent."""

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("CountryOfOfferIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        speak_output = "We will collect your most up-to-date location information two to three weeks prior to your start date. " \
                       "If you are not located in the country of your offer and do not believe you will be able to relocate due to travel restrictions, " \
                       "a recruiter will then reach out before your current start date to assess the travel landscape and possible options."
        reprompt_text = random.choice(FAQ_REPROMPT_TEXTS)
        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)

        return (response_builder.speak(speak_output).ask(
            reprompt_text).response)


class VirtualEventsIntentHandler(AbstractRequestHandler):
    """Handler for Virtual Events Intent."""

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("VirtualEventsIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        speak_output = "We will host a variety of virtual intern events. You will receive additional details about events once you start at Amazon."
        reprompt_text = random.choice(FAQ_REPROMPT_TEXTS)
        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)

        return (response_builder.speak(speak_output).ask(
            reprompt_text).response)


class WorkingHoursIntentHandler(AbstractRequestHandler):
    """Handler for Working Hours Intent."""

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("WorkingHoursIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        speak_output = "Please work directly with your manager to decide what works best for you and your team."
        reprompt_text = random.choice(FAQ_REPROMPT_TEXTS)
        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)

        return (response_builder.speak(speak_output).ask(
            reprompt_text).response)


class TaxesIntentHandler(AbstractRequestHandler):
    """Handler for Taxes Intent."""

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("TaxesIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        speak_output = "Your state or local tax responsibilities, as well as tax withholding on your earnings, are based on both your legal resident address " \
                       "and the address where you work. When the location of your legal residence differs from the location where you work, you may be subject to tax withholding " \
                       "in the location of your legal residence, the location where you work, or both. In reporting and paying your taxes, you should seek advice from your own " \
                       "tax consultant or adviser, as taxation is both personal and specific to the circumstances of the individual."
        reprompt_text = random.choice(FAQ_REPROMPT_TEXTS)
        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)

        return (response_builder.speak(speak_output).ask(
            reprompt_text).response)


class StartDateIntentHandler(AbstractRequestHandler):
    """Handler for Start Date Intent."""

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("StartDateIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        speak_output = "To reduce potential issues with immigration and/or background checks, we recommend keeping your start dates as is; " \
                       "however, if there has been a change in your availability, please reach out to your recruiting team to request the change."
        reprompt_text = random.choice(FAQ_REPROMPT_TEXTS)
        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)

        return (response_builder.speak(speak_output).ask(
            reprompt_text).response)


class AmazonGearIntentHandler(AbstractRequestHandler):
    """Handler for Amazon Gear Intent."""

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("AmazonGearIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        speak_output = "Each intern receives a credit to use in our Amazon gear store where you choose the Amazon gear you prefer. " \
                       "From hoodies and T-shirts to bags and water bottles."
        reprompt_text = random.choice(FAQ_REPROMPT_TEXTS)
        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)

        return (response_builder.speak(speak_output).ask(
            reprompt_text).response)


class MentorIntentHandler(AbstractRequestHandler):
    """Handler for Mentor Intent."""

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("MentorIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        speak_output = "In addition to your manager, you will have a mentor and an onboarding buddy."
        reprompt_text = random.choice(FAQ_REPROMPT_TEXTS)
        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)

        return (response_builder.speak(speak_output).ask(
            reprompt_text).response)


class TimeOffIntentHandler(AbstractRequestHandler):
    """Handler for Time Off Intent."""

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("TimeOffIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        speak_output = "North America-based interns are eligible for sick time, but not eligible for paid vacation."
        reprompt_text = random.choice(FAQ_REPROMPT_TEXTS)
        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)

        return (response_builder.speak(speak_output).ask(
            reprompt_text).response)


class EndDateIntentHandler(AbstractRequestHandler):
    """Handler for End Date Intent."""

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("EndDateIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        speak_output = "Please speak with your manager about the process to extend your internship, or to end it early. " \
                       "Provide your manager with 30 days notice of this change when possible."
        reprompt_text = random.choice(FAQ_REPROMPT_TEXTS)
        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)

        return (response_builder.speak(speak_output).ask(
            reprompt_text).response)


class AWSEducateIntentHandler(AbstractRequestHandler):
    """Handler for AWS Educate Intent."""

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("AWSEducateIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        speak_output = "AWS Educate is a global Amazon initiative that helps accelerate cloud-learning and prepares students for the cloud-enabled jobs of tomorrow. " \
                       "We encourage you to join AWS Educate before your internship begins to gain a solid understanding of how cloud computing is a part of many jobs across Amazon."
        reprompt_text = random.choice(FAQ_REPROMPT_TEXTS)
        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)

        return (response_builder.speak(speak_output).ask(
            reprompt_text).response)


class AWSAcademyIntentHandler(AbstractRequestHandler):
    """Handler for AWS Academy Intent."""

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("AWSAcademyIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        speak_output = "AWS Academy provides higher education institutions around the world with AWS-authored courses that prepare students for cloud careers. " \
                       "As Amazon interns, you are invited you to take AWS Academy Cloud Foundations, an online course that will introduce you to AWS core services, security, " \
                       "architecture, pricing, and support. With approximately 20 hours of content, this course will help prepare you to take the AWS Certified Cloud Practitioner exam."
        reprompt_text = random.choice(FAQ_REPROMPT_TEXTS)
        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)

        return (response_builder.speak(speak_output).ask(
            reprompt_text).response)


class TeamChangeIntentHandler(AbstractRequestHandler):
    """Handler for Team Change Intent."""

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("TeamChangeIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        speak_output = "This placement is final; however, Amazon business needs can change and if your placement is impacted, we will notify you as soon as possible. " \
                       "We encourage you to keep an open mind with your first role. Amazon will provide many opportunities and this is Day 1 in your journey!"
        reprompt_text = random.choice(FAQ_REPROMPT_TEXTS)
        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)

        return (response_builder.speak(speak_output).ask(
            reprompt_text).response)


class TeamInfoIntentHandler(AbstractRequestHandler):
    """Handler for Team Info Intent."""

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("TeamInfoIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        speak_output = "You will be connected with your manager before your start date. When you are connected with your manager, " \
                       "share a little about yourself and career interests, including your strengths, skills you would like to develop, topics you hope to gain exposure to, etc."
        reprompt_text = random.choice(FAQ_REPROMPT_TEXTS)
        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)

        return (response_builder.speak(speak_output).ask(
            reprompt_text).response)


class PortalChangesIntentHandler(AbstractRequestHandler):
    """Handler for Portal Changes Intent."""

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("PortalChangesIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        speak_output = "You may see changes in your portal as the Student Programs team kicks off your onboarding, but the enclosed location details are final."
        reprompt_text = random.choice(FAQ_REPROMPT_TEXTS)
        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)

        return (response_builder.speak(speak_output).ask(
            reprompt_text).response)


class VirtualDurationIntentHandler(AbstractRequestHandler):
    """Handler for Virtual Duration Intent."""

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("VirtualDurationIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        speak_output = "Interns will work virtually for the duration of their internship."
        reprompt_text = random.choice(FAQ_REPROMPT_TEXTS)
        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)

        return (response_builder.speak(speak_output).ask(
            reprompt_text).response)


#################################################################
################ Internship Reminders Handlers ##################
#################################################################

class SubscribeRemindersIntentHandler(AbstractRequestHandler):
    """
    Handler for Subscribe Reminders Intent. This listens to the 'subscribe to internship reminders' utterance
    after launch and routes the skill to the Start Date ConfirmationIntent handler.
    """

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("SubscribeRemindersIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        speak_output = "I can remind you of things to do as your internship comes closer. Please tell me your start date."
        reprompt_text = "I started working for Amazon on November Sixth Twenty Fourteen. When do you start?"
        
        attributes_manager = handler_input.attributes_manager
        
        if attributes_manager.persistent_attributes["start_day_attributes"] is not None:
            speak_output = "It looks like you have already subscribed to reminders. Would you like to do anything else?"
            reprompt_text = "Can I help you with something else?"
            
        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)

        return (response_builder.speak(speak_output).ask(
            reprompt_text).response)


class StartDateConfirmationIntentHandler(AbstractRequestHandler):
    """Handler for Hello World Intent."""

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        
        attributes_manager = handler_input.attributes_manager
        is_not_subscribed = True if attributes_manager.persistent_attributes["start_day_attributes"] is None else False
        
        return is_not_subscribed and ask_utils.is_intent_name("StartDateConfirmationIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response

        # Assign the slots for the start date
        slots = handler_input.request_envelope.request.intent.slots
        year = slots["year"].value
        month = slots["month"].value
        day = slots["day"].value
        start_day_attributes = {
            "year": year,
            "month": month,
            "day": day,
            "reminders_already_received": []
        }

        attributes_manager = handler_input.attributes_manager
        attributes_manager.persistent_attributes["start_day_attributes"] = start_day_attributes
        attributes_manager.save_persistent_attributes()
        
        # TODO: Add a check for if somoene says a start date in the past.

        speak_output = 'Thanks, I will remember your start date of {month} {day} {year}.'.format(
            month=month, day=day, year=year)
        reprompt_text = "Do you have other pre onboarding questions?"
        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)

        return (response_builder.speak(speak_output).ask(
            reprompt_text).response)


class GetInternshipRemindersIntentHandler(AbstractRequestHandler):
    """Handler for Get Internship Reminders Intent"""
        
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        
        return ask_utils.is_intent_name("GetInternshipRemindersIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        logger.info("Calling GetInternshipRemindersIntent handler")
        
        persistent_attributes = handler_input.attributes_manager.persistent_attributes
        
        validate_persistent_attributes(persistent_attributes)
        
        start_day_attributes = persistent_attributes["start_day_attributes"] 
        logger.info(f"got start day attributes {start_day_attributes}")
        
        if start_day_attributes is None:
            speak_output = "Sorry, you need to subscribe to internship reminders first."
            reprompt_text = "You can say, subscribe to internship reminders. Or you can ask me another question."
            
            response_builder = handler_input.response_builder
            add_apl(handler_input, response_builder, speak_output)

            return (response_builder.speak(speak_output).ask(
                reprompt_text).response)
            
        reminders_already_received = start_day_attributes["reminders_already_received"]
        
        possible_reminders = {
            "45d_r1": "Your background check. ",
            "30d_r1": "Immigration processing, if it applies. ",
            "30d_r2": "Your managers contact information. ",
            "30d_r3": "Relocation information from Graebal. ",
            "14d_r1": "Your my docs portal email. ",
            "3d_r1": "Information about new hire orientation. "
        }
        
        
        initial_string = "Here are your current reminders. Contact offers on boarding if you have not heard about "
        
        the_following = ""
        if len(reminders_already_received) > 1:
            the_following = "the following. "
            
        if len(reminders_already_received) == 0:
            speak_output = "You have no reminders. "

            response_builder = handler_input.response_builder
            add_apl(handler_input, response_builder, speak_output)
            return (response_builder.speak(speak_output).ask(
                reprompt_text).response)
            

        reminders_string = ""
        for reminder in reminders_already_received:
            reminders_string += possible_reminders[reminder]

        speak_output = initial_string + the_following + reminders_string
        reprompt_text = "You can say, subscribe to internship reminders. Or you can ask me another question."

        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)

        return (
            response_builder.speak(speak_output).ask(reprompt_text).response)


#################################################################
################### Roommate Survey Handlers ####################
#################################################################


class StartedRoommateSurveyIntentHandler(AbstractRequestHandler):
    """Handler on opening Roommate Survey Intent"""

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return (ask_utils.is_request_type("IntentRequest")(
            handler_input) and ask_utils.is_intent_name("RoommateSurveyIntent")(
            handler_input) and handler_input.request_envelope.request.dialog_state.to_str() == "'STARTED'")

    def handle(self, handler_input):
        currentIntent = handler_input.request_envelope.request.intent
        data = get_user_data(handler_input)
        logger.info(str(data))
        if not data:
            permissions = ['alexa::profile:name:read', 'alexa::profile:email:read', 'alexa::profile:mobile_number:read']
            message_str = """Please enable Contact Information permissions through the Amazon Alexa app. You should see a card in the
                            home section of the Alexa app titled permissions requested. You may have scroll to find it. 
                            If you cannot find the card, then you would have to go to the skill page and click on settings"""
            
            response_builder = handler_input.response_builder
            add_apl(handler_input, response_builder, message_str)
            
            return ( response_builder.speak(message_str)
                        .set_card(AskForPermissionsConsentCard(permissions))
                        .response
                    )
        attributes_manager = handler_input.attributes_manager
        roommate_survey_answers = attributes_manager.persistent_attributes["roommate_survey_attributes"]
        #if not roommate_survey_answers:
        #    return ( handler_input.response_builder
        #                .speak(
        #                    'You have already completed the Roommate Matching Survey'
        #                ).response
        #            
        #    )
        speak_output = "Welcome to the Day Zero Roommate Matching Survey! Please try to answer the questions in the simplest manner possible. Redoing this survey will give you a new set of roommate matches"
        #response_builder = handler_input.response_builder
        #add_apl(handler_input, response_builder, speak_output)
        return (
            handler_input.response_builder.speak(speak_output).add_directive(
                DelegateDirective()).response)


class InProgressRoommateSurveyIntentHandler(AbstractRequestHandler):
    """Auto Delegation"""

    def can_handle(self, handler_input):
        return (ask_utils.is_request_type("IntentRequest")(
            handler_input) and ask_utils.is_intent_name("RoommateSurveyIntent")(
            handler_input) and handler_input.request_envelope.request.dialog_state.to_str() == "'IN_PROGRESS'")

    def handle(self, handler_input):
        currentIntent = handler_input.request_envelope.request.intent
        return (handler_input.response_builder.add_directive(
            DelegateDirective()).response)


# will need to create and authenticate Boto3 object
# use AWS educate credentials ? - Rohan
class CompletedRoommateSurveyIntentHandler(AbstractRequestHandler):
    """Handler completion Roommate Survey Intent"""

    def can_handle(self, handler_input):
        return (ask_utils.is_request_type("IntentRequest")(
            handler_input) and ask_utils.is_intent_name("RoommateSurveyIntent")(
            handler_input) and handler_input.request_envelope.request.dialog_state.to_str() == "'COMPLETED'")

    def handle(self, handler_input):
        speak_output = """Thank you for completing the survey! You can check your potential roommate options
                        by asking Show me a Potential Roommate."""
                        
        ud = get_user_data(handler_input)
        item = {'name-phone-email': stringify_user_data(ud),
            'office': ask_utils.request_util.get_slot(handler_input,
                                                            "office").value,
            'gender': ask_utils.request_util.get_slot(handler_input,
                                                      "gender").value,
            'cleanliness': ask_utils.request_util.get_slot(handler_input,
                                                           "cleanliness").value,
            'active_hours': ask_utils.request_util.get_slot(handler_input,
                                                            "active_hours").value,
            'personality': ask_utils.request_util.get_slot(handler_input,
                                                           "personality").value,
            'roommate_gender_preference': ask_utils.request_util.get_slot(handler_input,
                                                                 "roommate_gender_preference").value,
            'cleanliness_preference': ask_utils.request_util.get_slot(handler_input,
                                                           "cleanliness_level_preference").value,
            'room_type': ask_utils.request_util.get_slot(handler_input,
                                                           "room_style_preference").value,
            'active_hours_preference': ask_utils.request_util.get_slot(handler_input,
                                                            "active_hours_preference").value,
            'personality_preference': ask_utils.request_util.get_slot(handler_input, 
                                                            "personality_preference").value,
            'interesting_fact': ask_utils.request_util.get_slot(handler_input, 
                                                            "interesting_fact").value
        }
        item['matches'] = []
        item["bucket_str"] = get_bucket_str(item)
        
        attributes_manager = handler_input.attributes_manager
        match_data = attributes_manager.persistent_attributes["roommate_survey_attributes"]
        if match_data:
            if match_data["survey_result"] != item:
                match_data["matches"] = generate_compatible_roommates(item)
        else:
            match_data = {}
            match_data["survey_result"] = item
            match_data["matches"] = generate_compatible_roommates(item)
        match_data["viewing"] = 0
        attributes_manager.persistent_attributes["roommate_survey_attributes"] = match_data
        attributes_manager.save_persistent_attributes()
        

        #output = put_user_row(item)
        #logger.info(str(output))
        #output = insert_into_compatability_bucket(item)
        #logger.info(output)
        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)
        return (response_builder.speak(speak_output).response)


class PotentialRoommateIntentHandler(AbstractRequestHandler):
    """Handler for Amazon Gear Intent."""

    def can_handle(self, handler_input):
       # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("PotentialRoommateIntent")(handler_input)

    def handle(self, handler_input):
       # type: (HandlerInput) -> Response
        speak_output = ""
        attributes_manager = handler_input.attributes_manager
        match_data = attributes_manager.persistent_attributes["roommate_survey_attributes"]
        logger.info(match_data)
        if not match_data:
            speak_output = "You have not completed the roommate matching survey. You can do so by asking Help me find a roommate"
            response_builder = handler_input.response_builder
            add_apl(handler_input, response_builder, speak_output)
            return (response_builder.speak(speak_output).response)
        else:
            if match_data["viewing"] < (len(match_data["matches"]) - 1):
                match_data["viewing"] += 1
            else:
                match_data["viewing"] = 0
            attributes_manager.persistent_attributes["roommate_survey_attributes"] = match_data
            attributes_manager.save_persistent_attributes()
            match = match_data["matches"][match_data["viewing"]]
            speak_output = "A potential roommate match is {}. ".format(match["name"])
            speak_output += "{} has provided the following interesting fact: {} ".format(
                match["name"], match["interesting_fact"]
            )
            speak_output += ". More information about {} can be found on the card on your alexa app.".format(match["name"])
            speak_output += " For another potential roommate match please say Show me a Potential roommate"
            card_title = "Potential Roommate: {}".format(match["name"])
            card_text = "Gender: {} \r\n".format(match["gender"])
            card_text += "Office: {} \r\n".format(match["office"])
            card_text += "Room Style Preference: {} \r\n".format(match["room_type"])
            card_text += "Cleanliness: {} \r\n".format(match["cleanliness"])
            card_text += "Active Hours: {} \r\n".format(match["active_hours"])
            card_text += "Personality: {} \r\n".format(match["personality"])
            card_text += "Contact Information: {} {} \r\n".format(match["email"], match["random_phone"])
            card_text += "Interesting Fact Provided: {}".format(match["interesting_fact"])
            response_builder = handler_input.response_builder
            add_apl(handler_input, response_builder, speak_output)
            return response_builder.speak(speak_output).set_card(
                SimpleCard(card_title, card_text)).response

#################################################################
#################### Fun Fact Handlers ####################
#################################################################


class FunFactIntentHandler(AbstractRequestHandler):
    """Handler for Portal Changes Intent."""
    def __init__(self):
        self.facts = [
            "Amazon was originally called Cadabra, like the magic term \"abracadabra.\"",
            "The company name, Amazon, is a reference to the river in South America. The idea was that their selection of books would be vast and wide, just like the world's largest river.",
            "Amazon started out as a bookstore run out of Jeff Bezos’s garage.",
            "In 2018, AWS made Amazon $7.3 billion in revenue.",
            "Amazon owns 41 subsidiaries and brands including Audible, Goodreads, Ring, Twitch, and Whole Foods Market.",
            "Jeff Wilke, who was the operations manager in the early 2000s, had an interesting method to let out frustration. He would encourage his employees who had just accomplished a goal to call him, close their eyes, and yell at the top of their lungs like a primal scream.",
            "Amazon is a pet-friendly environment; there are about 6,000 dogs that work at Amazon’s campus in Seattle.",
            "The Spheres, Amazon’s Seattle campus, are filled with over 400 species of plants from around the world.",
            "Amazon was founded by Jeff Bezos in Bellevue, Washington, on July 5, 1994.",
            "In the early days of Amazon, a bell would ring in the office every time someone made a purchase, and everyone would gather around to see if they knew the customer.",
            "Initially, book distributors required retailers to order 10 books at a time, and Amazon didn't need that much inventory yet. So, the team discovered a loophole: they would order one book they needed, and nine copies of an obscure lichen book, which was always out of stock.",
            "In the early days, Bezos held meetings at Barnes and Noble.",
            "Jeff Bezos purchased a $40,000 skeleton of an Ice Age cave bear and displayed it in the lobby of the company's headquarters. Next to it was a sign that read \"Please Don't Feed The Bear.\" It's still there today.",
            "Before Google had \"Street View\" in 2007, Amazon had \"Block View\" in 2004, which was essentially the same idea. However, Amazon dropped the project in 2006.",
            "\"Fiona\" was the original code-name for Amazon's Kindle. However, it was renamed to Kindle, because it evoked the idea of starting a fire.",
            "Jeff Bezos wanted to call Amazon business MakeItSo.com.",
            "Amazon once cleared out a Toys “R” Us to have holiday inventory.",
            "Amazon’s fastest delivery may have been 23 minutes; the same-day Prime service delivered an Easy-Bake Oven in Manhattan.",
            "Amazon’s first customer got a building named after him.",
            "Amazon sells tiny houses; they offer a home kits for around $26,000 that feature a 20-foot by 40-foot living space, including a kitchen and a bathroom.",
            "The first book Amazon ever sold was called Fluid Concepts and Creative Analogies by Doug Hofstadte.",
            "One of Amazon’s earliest investors were Bezos’ own parents. Bezos’ folks took out $300,000 from their retirement savings to invest in their ambitious son’s shiny new internet startup.",
            "While running Amazon out of Bezos’ garage, the new company’s computer servers took up so much power that Bezos and his wife couldn’t plug in as much as a hair dryer in their house without risking blowing a fuse.",
            "Amazon once listed a book about the genetic makeup of flies for over $23 million. This crazy price happened because the price of the book was set automatically by an algorithm that listed the price relative to the cost of another Amazon source store.",
            "Amazon’s “One Click” button that allows consumers to purchase items with only a single click of the mouse is actually a patented and trademarked operation. Apple, who also offers “One Click” purchases to its customers, is doing so through a licensing agreement with Amazon. This means that even while customers are using Apple products, Amazon is getting paid.",
            "Amazon ships a lot of packages. But would you believe that as of 2013, the company shipped 1.6 million packages, per day!",
            "Amazon originally constructed employee desks out of inexpensive doors as a cost savings measure.",
            "Amazon’s logo is a yellow arrow that looks like a smile underneath the Amazon lettering. Originally, the smile design was meant to convey that, “we’re happy to deliver anything, anywhere,” but an Amazon press release expanded the meaning by emphasizing that the beginning of the smile/arrow started at the “A” and ended on the “Z “of “Amazon,” indicating that Amazon had everything to fulfill its customers’ needs from A to Z.",
            "In August 2013, the Amazon website went down for 40 minutes. While this may seem like a small blip in time, it ended up costing Amazon an estimated $4.8 million, or $120,000 per minute."
        ]

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("FunFactIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        speak_output = random.choice(self.facts)
        reprompt_text = random.choice(FAQ_REPROMPT_TEXTS)

        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)

        return (response_builder.speak(speak_output).ask(
            reprompt_text).response)


#################################################################
#################### Fallback Email Handlers ####################
#################################################################


class FallbackIntentHandler(AbstractRequestHandler):
    """Handler for Fallback Intent."""

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("AMAZON.FallbackIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        speak_output = "Sorry, we don't have an answer to your question yet. You can send an email to ASP Offers Onboarding by saying \"Send email\", followed by your question and you should receive a response within 5 business days."
        reprompt_text = random.choice(FAQ_REPROMPT_TEXTS)
        
        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)

        return (response_builder.speak(speak_output).ask(
            reprompt_text).response)

class EmailIntentHandler(AbstractRequestHandler):
    """Handler for Email Intent."""

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("EmailIntent")(handler_input)

    def create_boto_client(self, aws_region):
        creds = {}
        
        with open('aws_ses_creds.json') as json_file:
            creds = json.load(json_file)
            
        return boto3.client(
            'ses',
            region_name=aws_region,
            aws_access_key_id=creds['aws_access_key_id'],
            aws_secret_access_key=creds['aws_secret_access_key'],
        )

    def send_email(self, address, name, question):
        # Set up email fields
        SENDER = "bighousing2021@gmail.com"
        TO_RECIPIENT = "bighousing2021@gmail.com" # asp-offersonboarding@amazon.com
        REPLYTO_RECEIPIENT = address
        CC_RECIPIENT = address

        SUBJECT = "Question from Intern via Day Zero"
        BODY_TEXT = """Hello,
                    
                    An intern has asked a question via the Alexa Day Zero skill which was unanswered.
                    Here is {}'s question:
                        "{}"
                    
                    Please respond to this email with an answer by hitting "Reply".
                    
                    Thanks,
                    Day Zero""".format(name, question)
        BODY_HTML = """
        <html>
        <head></head>
        <body>
          <p>Hello,</p><br>
          <p>An intern has asked a question via the Alexa Day Zero skill which was unanswered.</p>
          <p>Here is {}'s question:</p>
          <p>&emsp;&emsp;"{}"</p><br>
          <p>Please respond to this email with an answer by hitting "Reply".</p><br>
          <p>Thanks,</p>
          <p>Day Zero</p>
        </body>
        </html>
                    """.format(name, question)
        CHARSET = "UTF-8"
        
        AWS_REGION = "us-east-2"
        client = self.create_boto_client(AWS_REGION)

        # Try to send the email.
        try:
            # Provide the contents of the email.
            response = client.send_email(
                Destination={
                    'ToAddresses': [
                        TO_RECIPIENT,
                    ],
                    'CcAddresses': [
                        address,
                    ],
                },
                Message={
                    'Body': {
                        'Html': {
                            'Charset': CHARSET,
                            'Data': BODY_HTML,
                        },
                        'Text': {
                            'Charset': CHARSET,
                            'Data': BODY_TEXT,
                        },
                    },
                    'Subject': {
                        'Charset': CHARSET,
                        'Data': SUBJECT,
                    },
                },
                ReplyToAddresses=[
                    REPLYTO_RECEIPIENT,
                ],
                Source=SENDER,
            )
        # Display an error if something goes wrong.
        except ClientError as e:
            logger.error(e.response['Error']['Message'])
            return False
        else:
            logger.info("Email sent! Message ID: {}".format(response['MessageId']))
            return True

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        reprompt_text = random.choice(FAQ_REPROMPT_TEXTS)
        
        if handler_input.request_envelope.request.intent.confirmation_status == IntentConfirmationStatus.CONFIRMED:
            # Get user's email address and name
            data = get_user_data(handler_input)
            logger.info(data)
            if not data:
                permissions = ['alexa::profile:name:read', 'alexa::profile:email:read', 'alexa::profile:mobile_number:read']
                message_str = """Please enable Contact Information permissions through the Amazon Alexa app. You should see a card in the
                                home section of the Alexa app titled permissions requested. You may have scroll to find it. 
                                If you cannot find the card, then you would have to go to the skill page and click on settings"""
                                 
                response_builder = handler_input.response_builder
                add_apl(handler_input, response_builder, message_str)
                
                return ( response_builder.speak(message_str)
                            .set_card(AskForPermissionsConsentCard(permissions))
                            .response
                        )
            address = data['email']
            name = data['name']
            
            # Get utterance that triggered intent
            question = ask_utils.request_util.get_slot(handler_input, "question").value
            
            # Generate speak output
            if self.send_email(address, name, question):
                speak_output = "Email sent to ASP Offers Onboarding, expect to receive a response within 5 business days."
            else:
                speak_output = "Sorry I was not able to send an email to ASP Offers Onboarding."
        else:
            speak_output = reprompt_text

        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)

        return (response_builder.speak(speak_output).ask(
            reprompt_text).response)


#################################################################
#################### Default intent handlers ####################
#################################################################


class HelpIntentHandler(AbstractRequestHandler):
    """Handler for Help Intent."""

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("AMAZON.HelpIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        speak_output = "You can say hello to me! How can I help?"

        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)

        return (response_builder.speak(speak_output).ask(
            speak_output).response)


class CancelOrStopIntentHandler(AbstractRequestHandler):
    """Single handler for Cancel and Stop Intent."""

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return (ask_utils.is_intent_name("AMAZON.CancelIntent")(
            handler_input) or ask_utils.is_intent_name("AMAZON.StopIntent")(
            handler_input) or ask_utils.is_intent_name("AMAZON.NoIntent"))

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        speak_output = "Thank you for using Day Zero. Goodbye!"
        
        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)

        return response_builder.speak(speak_output).set_should_end_session(True).response


class SessionEndedRequestHandler(AbstractRequestHandler):
    """Handler for Session End."""

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_request_type("SessionEndedRequest")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response

        # Any cleanup logic goes here.

        return handler_input.response_builder.response


class IntentReflectorHandler(AbstractRequestHandler):
    """The intent reflector is used for interaction model testing and debugging.
    It will simply repeat the intent the user said. You can create custom handlers
    for your intents by defining them above, then also adding them to the request
    handler chain below.
    """

    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_request_type("IntentRequest")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        intent_name = ask_utils.get_intent_name(handler_input)
        speak_output = "You just triggered " + intent_name + "."
        speak_output += " {} {}".format(
            ask_utils.is_intent_name("RoommateSurveyIntent")(handler_input),
            handler_input.request_envelope.request.dialog_state.to_str())

        return (handler_input.response_builder.speak(
            speak_output)# .ask("add a reprompt if you want to keep the session open for the user to respond")
                                              .response)


class CatchAllExceptionHandler(AbstractExceptionHandler):
    """Generic error handling to capture any syntax or routing errors. If you receive an error
    stating the request handler chain is not found, you have not implemented a handler for
    the intent being invoked or included it in the skill builder below.
    """

    def can_handle(self, handler_input, exception):
        # type: (HandlerInput, Exception) -> bool
        return True

    def handle(self, handler_input, exception):
        # type: (HandlerInput, Exception) -> Response
        logger.error(exception, exc_info=True)

        speak_output = "Sorry, I had trouble doing what you asked. Please try again."

        response_builder = handler_input.response_builder
        add_apl(handler_input, response_builder, speak_output)

        return (response_builder.speak(speak_output).ask(
            speak_output).response)


# The SkillBuilder object acts as the entry point for your skill, routing all request and response
# payloads to the handlers above. Make sure any new handlers or interceptors you've
# defined are included below. The order matters - they're processed top to bottom.

# Using S3 for now until we are onboarded to dynamoDB. To switch comment CustomSkillBuilder
# and uncomment StandardSkillBuilder. You will have to define the persistence_adapter for StandardSkillBuilder
# and configure dynamoDB. See
# https://developer.amazon.com/blogs/alexa/post/a47f25e9-3e87-4afd-b632-ff3b86febcd4/skill-builder-objects-to-customize-or-not-to-customize

# how to play with s3? https://github.com/alexa/skill-sample-python-first-skill/tree/master/module-3
sb = CustomSkillBuilder(persistence_adapter=s3_adapter, api_client=DefaultApiClient())
# sb = StandardSkillBuilder()

# see https://developer.amazon.com/en-US/docs/alexa/custom-skills/handle-requests-sent-by-alexa.html
sb.skill_id = "amzn1.ask.skill.f808d181-a0c9-485d-a143-586404b9f4ce"

sb.add_request_handler(LaunchRequestHandler())

sb.add_request_handler(StartedRoommateSurveyIntentHandler())
sb.add_request_handler(InProgressRoommateSurveyIntentHandler())
sb.add_request_handler(CompletedRoommateSurveyIntentHandler())

sb.add_request_handler(SubscribeRemindersIntentHandler())
sb.add_request_handler(StartDateConfirmationIntentHandler())
sb.add_request_handler(GetInternshipRemindersIntentHandler())

sb.add_request_handler(YesIntentHandler())
sb.add_request_handler(PreparingIntentHandler())
sb.add_request_handler(EquipmentIntentHandler())
sb.add_request_handler(CountryOfOfferIntentHandler())
sb.add_request_handler(VirtualEventsIntentHandler())
sb.add_request_handler(WorkingHoursIntentHandler())
sb.add_request_handler(TaxesIntentHandler())
sb.add_request_handler(StartDateIntentHandler())
sb.add_request_handler(AmazonGearIntentHandler())
sb.add_request_handler(MentorIntentHandler())
sb.add_request_handler(TimeOffIntentHandler())
sb.add_request_handler(EndDateIntentHandler())
sb.add_request_handler(AWSEducateIntentHandler())
sb.add_request_handler(AWSAcademyIntentHandler())
sb.add_request_handler(TeamChangeIntentHandler())
sb.add_request_handler(TeamInfoIntentHandler())
sb.add_request_handler(PortalChangesIntentHandler())
sb.add_request_handler(VirtualDurationIntentHandler())
sb.add_request_handler(FunFactIntentHandler())
sb.add_request_handler(EmailIntentHandler())
sb.add_request_handler(PotentialRoommateIntentHandler())

sb.add_request_handler(FallbackIntentHandler())
sb.add_request_handler(HelpIntentHandler())
sb.add_request_handler(CancelOrStopIntentHandler())
sb.add_request_handler(SessionEndedRequestHandler())
sb.add_request_handler(IntentReflectorHandler())
# make sure IntentReflectorHandler is last so it doesn't override your custom intent handlers

sb.add_exception_handler(CatchAllExceptionHandler())

lambda_handler = sb.lambda_handler()
