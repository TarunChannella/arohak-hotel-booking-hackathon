# AROHAK Hackathon Hiring — Hotel Booking Management System

> Authoritative transcription of the organizers' four-page printed problem
> statement, supplied by the candidate. The original images are not committed to
> this repository. This file is the requirements source of truth for the project.

## Objective

Build a hotel booking management system that lets users register and log in,
manage hotel rooms, search available rooms, make bookings and manage
cancellations.

## Challenge Structure

1. Mandatory MVP — 50 marks
2. Core Extensions — 13 marks
3. High-Value AI Extensions — 37 marks

Candidates must complete all three Mandatory MVP features first. After that,
they may choose Core Extensions, High-Value AI Extensions or both.
Maximum score: 100.

## I. Mandatory MVP — 50 Marks

### 1. User Authentication and Roles — 15 Marks

Implement registration, login and role-based access.

Registration fields:
- Name
- Email
- Password
- Role

Login fields:
- Email address
- Password

Roles and permissions:

ADMIN:
- Manage the hotel and rooms
- Manage availability
- Manage relevant bookings

RECEPTIONIST:
- View and manage rooms
- Manage availability
- View/manage bookings
- Manage cancellations
- Must not have Admin-level functionality

CUSTOMER:
- Browse rooms
- View room details
- Select dates
- Book rooms
- View their bookings
- Cancel according to cancellation rules
- Cannot modify room information

### 2. Single Hotel and Room Management — 15 Marks

The MVP initially supports one standalone hotel.

Hotel fields:
- Hotel ID
- Hotel name
- Address
- City
- Description
- Contact number
- Email address
- Status: ACTIVE or INACTIVE

Authorized staff can add, view, update, manage availability and deactivate
rooms.

Room fields:
- Room ID
- Room number
- Room type
- Number of guests/capacity
- Price per night
- Availability status
- Description
- Amenities

Inactive rooms cannot be booked.
Availability must consider existing bookings.

### 3. Customer Hotel Booking — 20 Marks

Customers can browse available rooms in the single hotel.

Search fields:
- Check-in date
- Check-out date
- Number of guests

Room listings must show:
- Room number
- Room type
- Price
- Capacity
- Description
- Amenities
- Availability

Booking fields:
- Booking ID
- Customer ID
- Organization ID
- Hotel ID
- Room ID
- Check-in date
- Check-out date
- Number of guests
- Booking date
- Total amount
- Booking status

Prevent conflicting or overlapping bookings.
Handle simultaneous booking attempts safely.

Booking confirmation must include:
- Booking ID
- Hotel
- Room
- Customer
- Dates
- Guests
- Total amount
- Booking status

Cancellation:
- A customer can directly cancel until 24 hours/one day before check-in.
- Example: for September 20 check-in, direct cancellation is permitted until
  September 19.
- After the deadline, the customer submits a cancellation request for staff
  review.

## II. Core Extensions — 13 Marks

### 4. Booking Management Dashboards — 3 Marks

Customer dashboard:
- Upcoming bookings
- Historical/completed bookings
- Cancelled bookings
- Booking details and status

Staff dashboard:
- Admins and Receptionists can view relevant customer bookings
- Search and filter bookings
- View details
- Manage bookings where permitted

Booking status values:
- CONFIRMED
- CANCELLED
- COMPLETED

A CANCELLATION_REQUESTED workflow may be used internally to implement the
required late-cancellation staff review.

### 5. Multi-Organization Architecture — 10 Marks

Upgrade the standalone system to support multiple isolated organizations.

Hierarchy:
Platform -> Organization -> Hotel -> Room -> Booking

PRODUCT_ADMIN:
- Create, manage and view organizations
- Manage appropriate platform-level information

ORGANIZATION_ADMIN:
- Manage only their organization
- Manage multiple hotels and rooms
- Assign Receptionists

Data isolation:
- Users access only authorized organizations and hotels
- Organization Admin cannot access another organization
- Receptionist accesses only assigned hotels
- Each hotel owns its rooms, availability, bookings and hotel information
- Customer flow:
  organization -> hotel -> rooms -> availability -> book

## III. High-Value AI Extensions — 37 Marks

### 6. AI Chatbot — Booking Management — 20 Marks

Build an AI-powered booking assistant connected to actual booking data.

Requirements:
- Understand natural-language booking intent
- Extract location
- Extract number of guests
- Extract check-in date
- Extract check-out date
- Example:
  "I need a room in Mumbai for 2 people from Sept 20 to Sept 23."
- Check actual room availability
- Never invent availability
- Assist with booking and management through chat
- Support upcoming bookings
- Support booking details
- Support cancellation assistance
- Use a controlled tool/API layer between AI and backend
- Do not provide unrestricted database access to the AI

Example controlled backend tools:
- search_rooms()
- check_availability()
- get_booking()
- create_booking()
- cancel_booking()

### 7. AI Chatbot — RAG Based on PDF — 17 Marks

Build a RAG chatbot that answers customer questions strictly using information
from an uploaded PDF.

The PDF may contain:
- Hotel policies
- Cancellation policies
- Check-in/check-out rules
- Amenities
- Room information
- FAQs
- Facilities
- Guidelines

Requirements:
- Answer questions such as check-in time, Wi-Fi availability, cancellation
  policy and parking
- Retrieve relevant PDF context
- Ground answers in the retrieved context
- If information is absent, say it is unavailable
- Do not invent answers
- If multiple hotels are supported, use only the selected hotel's PDF
- Suitable RAG technologies and approaches may be used

## Technology

Candidates may choose the frontend, backend, programming language, database,
authentication method and deployment method.

## AI-Assisted Development

AI-assisted development is allowed and encouraged.

Candidates remain responsible for understanding, testing and validating their
code and may be asked to explain their implementation and technical decisions.

END OF OFFICIAL REQUIREMENTS.
